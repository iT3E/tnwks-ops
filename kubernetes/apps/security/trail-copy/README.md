# trail-copy

The copy of the AWS trail, on homelab-wsl-02. A CronJob copies the new files
of the trail bucket to a volume, and sends each event of a file to this
site's Loki as one line of the security stream `cloudtrail`. Local copies stay
7 days. The trail, its bucket and the role that reads it are in
`infrastructure/terraform/modules/aws/security`.

## The stream

| What | Value |
| --- | --- |
| Loki | The Loki of homelab-wsl-02, through `loki-gateway.monitoring` |
| Labels | `security_stream="cloudtrail"`, and no other label: one stream for every account and Region |
| A line | One event of the trail, as AWS wrote it, as one line of JSON |
| The time of a line | `eventTime` of the event |
| The account and the Region of an event | In the line: `recipientAccountId` and `awsRegion` |

```logql
{security_stream="cloudtrail"} | json | recipientAccountId="654654262098" | eventName="ConsoleLogin"
```

The stream is the check `cloudtrail` of the source `stream` in
`security/registry/sources.yaml`: a cycle of 15 minutes, and late after 2
hours. The converter (`security/converter/README.md`) serves the time of its
newest line for `aws-management` and `aws-prod`. Loki keeps a security stream
90 days.

## The volume

The PersistentVolumeClaim `trail-copy`, mounted at `/data`:

```text
/data/files/AWSLogs/<organization>/<account>/CloudTrail/<Region>/<year>/<month>/<day>/<file>.json.gz
```

A copy has the key of its object in the bucket as its path. Its name says
where it is:

| Name | Meaning |
| --- | --- |
| `<file>.json.gz` | Loki has each event of the file |
| `<file>.json.gz.part` | The download or the send did not finish. The next run takes the file again |
| `<file>.json.gz.bad` | The file could not be read as a trail file. The run that found it failed, and the next runs pass it |

So the volume is also the record of what the job has done: there is no
second state. The job deletes a copy 7 days after its download.

## A run

Every 15 minutes (`app/cronjob.yaml`, `app/scripts/trail_copy.py`):

1. It lists the prefixes of the trail in the bucket, one for each account
   and Region.
2. It lists each prefix from the time of the newest local copy less 1 hour,
   for a file that AWS delivers late. The first run starts 6 days back.
3. It takes the files with no local copy, oldest first. For each one: the
   download, each event to Loki, then the final name.
4. It deletes the copies that are older than 7 days.

A run takes at most 500 files, and takes no new file after 10 minutes. The
next run goes on from there. A run that stops half way loses nothing: the
next run sends the same file again, and a query of Loki gives a line one
time (the same time and the same text).

| Case | What the job does |
| --- | --- |
| Loki or S3 does not answer, or the session is refused | The run fails (`KubeJobFailed`). The next run takes the same files |
| An event older than 7 days, or more than 1 hour behind the newest line of the stream | Loki refuses the line. The run counts the batch in its last line and goes on |
| An event over 200 KiB | The line goes without `requestParameters`, `responseElements`, `additionalEventData` and `serviceEventDetails`, with `"truncated": true`. The local copy has the whole event |
| The Secret of the session still has its placeholder | The run says so and ends well |

The script reads a file one event at a time, so a large file stays inside
the 128 MiB of the job (`../README.md`, "Memory").

## What it reads and reaches

- **The session.** The Secret `aws-security-trail-sts` of the credential
  refresher (`../aws-credentials`): the role that lists the trail bucket and
  reads its objects, and nothing else. A new pod each run, so each run has
  the newest session.
- **The network.** `app/networkpolicy.yaml`: DNS, the host name of the trail
  bucket in its Region, and the gateway pods of this site's Loki. No API
  server (the pod has no token), and nothing calls it.
- **The settings.** `SECURITY_TRAIL_BUCKET` in the cluster-settings of
  homelab-wsl-02: the output `security_trail_bucket` of the AWS Terraform
  root.

The script uses the Python standard library only, and signs its two kinds
of S3 request (list, and read an object) itself. Its tests are in
`security/tests/test_trail_copy.py`, with the two examples of a signature
from the Amazon S3 API reference.

## To start it, and the way back

It needs the trail and its roles (the AWS Terraform root), and the
credential refresher with its certificate (`../aws-credentials`): a cluster
lists that app first, because the pod of this one does not start without the
Secret of the session. The pull request that lists `ks.yaml` on a
cluster also adds `stream` to the `sources` of the AWS systems in
`security/registry/systems.yaml`, so the rule for a late result watches the
stream from that merge.

To stop it, remove its line from `kubernetes/clusters/wsl-02/apps/kustomization.yaml`
and `stream` from the two systems, and run the generator. Flux removes the
CronJob and the volume. The lines in Loki stay until their 90 days end, and
the files stay in the bucket.
