# trail-copy

The copy of the AWS trail, on homelab-wsl-02. A CronJob copies the new files
of the trail bucket to a volume, and sends each event of a file to this
site's Loki as one line of the security stream `cloudtrail`. Local copies stay
7 days. The trail, its bucket and the role that reads it are in
`infrastructure/terraform/modules/aws/security`.

## Before the merge that lists it

The pull request that lists `ks.yaml` on homelab-wsl-02 also adds `stream` to
the `sources` of `aws-management` and `aws-prod`. Merge it only when each of
these exists:

1. The apply of the AWS Terraform change: the organization trail, its bucket
   (`SECURITY_TRAIL_BUCKET` in the cluster-settings) and the role
   `tnwks-rolesanywhere-security-trail`.
2. The credential refresher on homelab-wsl-02: the owner's two certificates,
   the two profile settings, and `../aws-credentials/ks.yaml` in
   `kubernetes/clusters/wsl-02/apps/kustomization.yaml`
   (`../aws-credentials/README.md`, "To start it").
3. A real session in the Secret `aws-security-trail-sts`: its annotation
   `security.tnwks.us/expires` has a time.

If it merges early:

- With no Secret, the pod of each Job can not start
  (`CreateContainerConfigError`). Each Job fails at `activeDeadlineSeconds`
  (14 minutes), and `KubeJobFailed` fires and stays.
- With the placeholder `pending` in the Secret, each run ends well and copies
  nothing.
- In both cases Loki has no line of the stream, so `SecurityResultLate`
  fires for `aws-management` and `aws-prod` from the merge, and stays.

Both alerts end by themselves when the session exists and a run sends lines.

The first run goes up to 6 days back. A run takes at most 500 files, oldest
first, so the newest line in Loki is old until the copy reaches the last 2
hours. If the trail ran for some days before the merge, `SecurityResultLate`
can fire for both accounts for some hours after it. At 4 active prefixes with
a file each 5 minutes (assumed), 6 days are about 7,000 files: about 15 runs,
or 4 hours.

## The stream

| What | Value |
| --- | --- |
| Loki | The Loki of homelab-wsl-02, through `loki-gateway.monitoring` |
| Labels | `security_stream="cloudtrail"`, `aws_account` (the account ID) and `aws_region`: one stream for each account and Region, which is one prefix of the bucket |
| A line | One event of the trail, as AWS wrote it, as one line of JSON |
| The time of a line | `eventTime` of the event |

```logql
{security_stream="cloudtrail", aws_account="<account ID>"} | json | eventName="ConsoleLogin"
```

The stream is the check `cloudtrail` of the source `stream` in
`security/registry/sources.yaml`: a cycle of 15 minutes, and late after 2
hours. The converter (`security/converter/README.md`) asks for
`{security_stream="cloudtrail"}`, which selects each stream of the trail, so
its newest line is the time of the last result of `aws-management` and
`aws-prod`. Loki keeps a security stream 90 days. The two labels are not the
names of systems, so the registry and the stream map do not change. A check
for each account, with `aws_account`, is a later step.

### Why one stream for each account and Region

Loki refuses a line that is more than 1 hour older than the newest line of its
stream, and a line that is older than 7 days. The job keeps this order:

- It sends the files of one prefix in the order of the time in their key. A
  run sends the files of all prefixes in that order too.
- A later run starts at the newest copy of the prefix, less 2 hours.
- CloudTrail delivers a file of a prefix about each 5 minutes, with the events
  of those minutes.

So in the stream of a prefix a new line is rarely more than some minutes older
than the newest line. The order between two prefixes does not matter: a quiet
prefix that the job lists 4 hours later goes to a stream of its own, and Loki
takes it. In one stream for the whole trail, Loki would refuse it.

Loki still refuses a line when CloudTrail delivers an event more than about 1
hour after a newer event of its account and Region. Then the file gets the
name `.bad` and the run fails (the table of cases below).

The order is also why a fault of the request stops the run and sets nothing
aside. If the job set files aside and went on, the newer files would move the
newest line of the stream forward, and Loki would refuse the files of the fault
for their time when they are sent again. So the job sets a file aside only for
a 400 about the time of its lines, or for a 400 that comes after Loki took
other batches of the same file. Loki 3.6 (the release that chart 7.6.0 of
`kubernetes/apps/monitoring/loki` runs) says `timestamp too old` or
`timestamp too new` for the first kind in its distributor, and `entry too far
behind` or `entry out of order` in its ingester. Each other 400, such as a
label that it can not parse or a limit, is a fault of the request.

## The volume

The PersistentVolumeClaim `trail-copy`, mounted at `/data`:

```text
/data/files/AWSLogs/<organization>/<account>/CloudTrail/<Region>/<year>/<month>/<day>/<file>.json.gz
/data/state.json
```

A copy has the key of its object in the bucket as its path. Its name says
where it is:

| Name | Meaning |
| --- | --- |
| `<file>.json.gz` | Loki has each event of the file |
| `<file>.json.gz.part` | The download or the send did not finish, or Loki refused the request. The next run takes the file again |
| `<file>.json.gz.bad` | Loki does not have each event of the file: the file can not be read as a trail file, Loki refused a line of it for its time, or Loki refused a batch after it took others of the file. The run that found it failed, and its log names the key. The next runs pass it |

So the volume is also the record of what the job has done. The job deletes a
copy 7 days after its download. The object stays in the bucket for 90 days.

`state.json` holds the list of prefixes and the time of the last list of each
prefix. It is a cache of what to list, and not a record of what is done:
without it, or with a file that does not fit, a run lists the prefixes and
each prefix, and loses nothing.

## A run

Every 15 minutes (`app/cronjob.yaml`, `app/scripts/trail_copy.py`):

1. It reads the list of prefixes from `state.json`: one for each account and
   Region. It lists them in the bucket again each hour.
2. It lists each prefix that is due, from the time of its newest local copy
   less 2 hours, for a file that AWS delivers late. The first run starts 6
   days back. A prefix is due:
   - in each run while its newest file is less than 1 hour old, and in the
     run after a list that found new files,
   - in each run when the whole trail had no new file for 90 minutes, half
     an hour before the stream is late,
   - else 4 hours after its last list.
3. It takes the files with no local copy, oldest first. For each one: the
   download, each event to the stream of its prefix, then the final name.
4. It deletes the copies that are older than 7 days.

A run takes at most 500 files, and takes no new file after 10 minutes. The
next run goes on from there. A run that stops half way loses nothing: the
next run sends the same file again, and a query of Loki gives a line one
time (the same time and the same text).

The last line of the log of a run counts the files, the events, the files set
aside as `.bad`, the files left (with `(stopped)` when the run stopped at a
file), the prefixes listed, and the list and get requests to S3.

| Case | What the job does |
| --- | --- |
| Loki or S3 does not answer, Loki answers 429, a 5xx or a 4xx that is not 400, or AWS refuses the session | The run fails (`KubeJobFailed`). The next run takes the same files |
| Loki answers 400 about the request, and not about the time of a line: a label that it can not parse, or a limit. It took no batch of the file | The run stops at that file and fails, with the key and the answer in its log. Nothing is set aside: each next run fails at the same file until the cause is fixed, and then the backlog goes to Loki in its order, while it is less than 7 days old |
| Loki refuses a line for its time: more than 1 hour behind the newest line of its stream, or older than 7 days | The other lines of the file go to Loki. The file gets `.bad`, and the run fails at its end with the key in its log. No later run sends it again, so `KubeJobFailed` fires one time for each case |
| Loki answers another 400 for one batch, after it took other batches of the file | The same as a refusal for the time |
| A file that can not be read as a trail file | The same |
| An event over 200 KiB | The line goes without `requestParameters`, `responseElements`, `additionalEventData` and `serviceEventDetails`, with `"truncated": true`. If it is still over, it keeps only the small fields that name the event (`eventName`, `eventTime`, `eventID`, `userIdentity` and others). The local copy has the whole event |
| S3 shows a file after a newer file of its prefix was copied | Up to 2 hours late, the next list of the prefix finds it. Loki takes its lines only if they are less than 1 hour behind the newest line of its stream. Else Loki refuses them for their time, the file gets `.bad`, and the run fails. So a file found late is not always a file in Loki. More than 2 hours late, no run lists it, and nothing alerts. The file stays in S3 for 90 days |
| A new account or Region | Its prefix joins at the next list of the prefixes, within about 1 hour |
| The Secret of the session still has its placeholder | The run says so and ends well |

To send a `.bad` file again, delete its `.bad` copy. The next list of its
prefix takes it only while it is less than 2 hours behind the newest copy of
the prefix, and Loki takes its lines only while they are less than 1 hour
behind the newest line of its stream.

The script reads a file one event at a time, so a large file stays inside
the 128 MiB of the job (`../README.md`, "Memory"). A file of 50,000 events
peaks at about 30 MiB.

## The cost

The S3 requests of this job, at the prices of S3 Standard in US West
(Oregon): $0.005 for each 1,000 LIST requests and $0.0004 for each 1,000 GET
requests ([Amazon S3 pricing](https://aws.amazon.com/s3/pricing/), read on
2026-11-13 in the AWS price list of 2026-11-09).

| Request | Count each month (30 days) |
| --- | --- |
| LIST, the prefixes | 30 × 96 a day: 4 lists (`AWSLogs/`, the organization, and each of 2 accounts) each hour |
| LIST, an active prefix | 30 × 96: each run |
| LIST, a quiet prefix | 30 × about 10: each 4 hours, and about 4 more after a day with a new file |
| LIST, when the whole trail had no new file for 90 minutes | Each quiet prefix, each run, until a file comes |
| GET | One for each new file |

```text
LIST each month = 30 × (96 + 96 × A + 10 × Q)
cost each month = LIST × 0.005 / 1000 + GET × 0.0004 / 1000
```

`A` is the count of active prefixes (a file in most hours), and `Q` the
count of quiet prefixes. 2 accounts and 17 Regions are 34 prefixes. The
hourly list of the prefixes is 2,880 of them, about $0.01:

| Active prefixes (assumed) | LIST each month | LIST cost |
| --- | --- | --- |
| 2 | 30 × (96 + 192 + 320) = 18,240 | $0.09 |
| 4 | 30 × (96 + 384 + 300) = 23,400 | $0.12 |

A list of each prefix in each run, as before, was (4 + 2 × 17) × 2,920 runs =
about 111,000 LIST requests, $0.55 each month. The GET requests are in the
cost of the design: one for each file, about $0.02 each month. The last line
of each run has the counts, so the first week measures `A` and `Q`.

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

## The way back

To stop it, remove its line from `kubernetes/clusters/wsl-02/apps/kustomization.yaml`
and `stream` from the two systems in `security/registry/systems.yaml`, and
run the generator. Flux removes the CronJob and the volume. The lines in Loki
stay until their 90 days end, and the files stay in the bucket.
