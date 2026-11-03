# transcriber

Speech on the cameras, transcribed and diarized, into Postgres. Fully local:
WhisperX on CPU, no cloud API. Manifests:
`kubernetes/apps/production/transcriber/`. Code and images: tnwks-transcriber
and tnwks-transcriber-web (`ghcr.io/it3e/tnwks-transcriber[-web]`, public and free of data). WSL-only,
like Frigate.

## Flow

1. Frigate's audio detection (CPU, `listen: [speech]`) publishes
   `frigate/<camera>/audio/speech` `ON` and `OFF`.
2. The transcriber reads each camera's audio from Frigate's go2rtc restream
   (no extra camera connection) into a 2-minute rolling buffer in RAM. ON
   opens a clip 5 s early; OFF closes it 2 s late. Sessions over 10 minutes
   are split.
3. The clip is saved as 16 kHz mono Opus on the `transcriber-audio` PVC and
   queued in Postgres. The worker transcribes one clip at a time: Whisper
   (`small.en`), word alignment, pyannote diarization, speaker matching.

Frigate only keeps footage 2 days, so the transcriber cuts its own clips
instead of reading Frigate's recordings.

## Cameras

| Camera | Audio | Stream used |
| --- | --- | --- |
| front door (`frigate_loryta_fdoor`) | PCMA, main stream only | `frigate_loryta_fdoor_hq` |
| south (`frigate_reolink_south`) | AAC | `frigate_reolink_south_lq` |
| west (`frigate_sv3c_west`) | none: advertises PCMA, sends no audio packets | none |
| kitchen, balcony, pool | none: no microphone | none |

Kitchen, balcony (Amcrest IP4M-1025E) and pool (Loryta) have no audio input:
each reports zero audio inputs over its API (`devAudioInput.cgi?action=getCollect`
returns `result=0`) and rejects `AudioEnable`, and their RTSP offers H264 only
(checked 2026-11). To add a camera that does carry audio, in one PR:

- give the input with audio the `audio` role and `audio.enabled: true` in
  `frigate/app/resources/config.yml`, and
- add `<camera>=<stream>` to `CAMERAS` in the transcriber HelmRelease.

Check first that the stream has an audio track (go2rtc's `/api/streams`
lists e.g. `PCMA` in the producer SDP): an audio role on a video-only stream
makes Frigate's audio ffmpeg restart every 10s.

Tune `audio.min_volume` per camera from Frigate's Debug view, Audio tab, if
speech is missed or background noise triggers it.

## Storage

| What | Where | Size | Kept |
| --- | --- | --- | --- |
| Clip audio | PVC `transcriber-audio` (local-path-bulk) | ~7 MB per hour of speech (Opus ~16 kb/s) | `AUDIO_RETENTION_DAYS` (90) |
| Transcripts, speakers, voiceprints | cnpg database `transcriber` | well under 1 GB a year at household volumes | forever |
| Models (~1 GB) | PVC `transcriber-models` | | until deleted |

The audio is not in Postgres: blobs would bloat the database, its WAL and
the S3 backups. The database row holds the clip's path. Transcripts are
backed up with the rest of the cnpg cluster (barman to S3); clip audio is
not backed up.

Query the transcripts directly:

```sql
SELECT started_at, camera, speaker, text FROM transcript
 WHERE started_at > now() - interval '1 day' ORDER BY started_at;

SELECT started_at, camera, speaker, text FROM transcript
 WHERE to_tsvector('english', text) @@ plainto_tsquery('english', 'dinner');
```

## The UI

`https://transcriber.internal.tnwks.us` (internal only), behind oauth2-proxy
and limited to the Cognito group `transcriber`, which only the admin is in
(`infrastructure/terraform/modules/aws/cognito.tf`). A session from before
the group existed has no groups yet: sign out of the SSO and in again.

| Page | What |
| --- | --- |
| Days | days with clips, newest first |
| A day | each clip: audio player, its voices, the transcript. Click a timestamp to play from there |
| Speakers | the people: add, rename, delete |
| Search | full text over all transcripts |

Two pieces, in one HelmRelease (`app/helmrelease.yaml`):

- `transcriber-frontend`: the Angular app,
  [iT3E/tnwks-transcriber-web](https://github.com/iT3E/tnwks-transcriber-web)
  (nginx, static files, its own pod).
- the `web` container in the transcriber pod: the JSON API the app calls
  under `/api` (same host; the ingress routes `/api` there). It shares the
  pod for the audio volume and loads no models.

## Speakers

Diarization labels (`SPEAKER_00`, ...) only mean something within one clip.
Consistent labels come from labeling a voice once in the UI: on a day's
page, **Play voice** plays only what one voice said, and **Label** assigns
it to a person (or creates one). That voice becomes a sample of the
person's voiceprint, and every clip, past and future, is re-matched. A
voice with too little speech can't be labeled (noisy embedding); pick a
clip where they say more.

Speakers have a neutral id (`person1`, ...) and a display name you set in
the UI. The names live only in the `transcriber` database (runtime data,
like Home Assistant's UI registries): never in this repo. Matching is cosine
similarity to the voiceprint, at least `SPEAKER_MATCH_THRESHOLD` (0.5), one
speaker per voice in a clip; the UI shows each match's score. The CLI
(`kubectl -n production exec deploy/transcriber -c app -- python3 -m
transcriber ...`) still has every command; the image README lists them.

## Privacy

This records and stores what people say at home. California requires every
party's consent to record a confidential conversation. Household members
and regular visitors should know the cameras record audio. The data stays on
the cluster and in the encrypted S3 backups.

## Checks and alerts

- `selfcheck` runs first (init container): env, writable volumes, database
  reachable with `vector` and migrations, MQTT login, `HF_TOKEN` set.
- `app/prometheusrule.yaml`: `TranscriberDown`, `TranscriberCaptureDown`
  (per camera, 30 m), `TranscriberMQTTDisconnected`, `TranscriberBacklog`
  (more than 30 clips waiting for 2 h), `TranscriberFailures`.
- Metrics: `transcriber_queue_pending`, `transcriber_models_loaded`,
  `transcriber_clips_total{camera}`, and real-time factor as
  `rate(transcriber_processing_seconds_sum[1h]) / rate(transcriber_audio_seconds_total[1h])`.

## First start

The models download into `transcriber-models` on the first start (Whisper
and wav2vec2 from Hugging Face and download.pytorch.org, pyannote from
Hugging Face). The pyannote model is gated: `HF_TOKEN` in
`app/secrets.sops.yaml` must belong to an account that accepted the
conditions at <https://huggingface.co/pyannote/speaker-diarization-community-1>.
Until the models load, capture runs and clips queue.
