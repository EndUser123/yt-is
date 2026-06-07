# Stage Reducer Output

## hotel_wifi_3plus3_shared_retry_canary_run26_current

- status: n/a, hygiene: n/a
- combined VPH: 0.00, wall: 0.0s

### Lane: a_hominidae_pro
- aggregate VPH: 0.00
- aggregate wall: 0.0s
- aggregate startup prepare: 0.0s
- aggregate startup notebook check: 0.0s
- aggregate startup notebook create: 0.0s
- aggregate startup notebook retire: 0.0s
- aggregate startup prepare cleanup: 0.0s
- aggregate setup: 0.0s
- aggregate setup excluding add: 0.0s
- aggregate add: 0.0s
- aggregate cleanup: 0.0s
- aggregate idle wait: 0.0s
- aggregate sr_age_avg: 0.0s
- success/fail/processed: 0/0/0

| Phase | Batch | Timestamp | Workers | elapsed(s) | setup(s) | setup_excl_add(s) | extract(s) | add(s) | cleanup(s) | sr_age(s) | command_failed | ready | **Lane Bottleneck** |
|-------|-------|-----------|---------|------------|----------|-------------------|------------|--------|------------|----------|----------------|-------|----------------|
| smoke | batch_01 | 20260603_030401 | 3 | 386.3 | 25.6 | 2.5 | 270.1 | 23.0 | 65.4 | 83.6 | 0 | 5 | stage-sum-suggested:extract [extract=70% of aggregate stage sum, recovered-unknown] |

| Worker | Worker Batch Count | Succeeded | Failed |
|--------|-------|-----------|--------|
| worker-01 | 1 | 5 | 20 |
| worker-01 | 1 | 5 | 20 |

| smoke | batch_02 | 20260603_031140 | 3 | 313.1 | 32.7 | 2.3 | 203.1 | 30.3 | 65.7 | 29.5 | 0 | 5 | stage-sum-suggested:extract [extract=70% of aggregate stage sum, recovered-unknown] |

| Worker | Worker Batch Count | Succeeded | Failed |
|--------|-------|-----------|--------|
| worker-01 | 1 | 5 | 20 |
| worker-01 | 1 | 5 | 20 |

### Command Attribution

- command completions: 120
- failures: 111 (92.5%)

- browser profile roots: P:\.data\yt-is\browser\notebooklm-pro
- browser profile directories: Profile
- worker state roots: P:\packages\yt-is\.logs\sharded_lane_series\hotel_wifi_3plus3_shared_retry_canary_run26_current\smoke\a_hominidae_pro\worker_states

| Worker | Profile | Commands | Ready | Failed | Source-Age-Cliff | Command-Failed | Avg Cmd(s) | Avg Ready Cmd(s) | Avg Failed Cmd(s) | Failure Rate |
|--------|---------|----------|-------|--------|------------------|----------------|------------|------------------|-------------------|--------------|
| worker-01 | ytis-pro-worker-01 | 120 | 9 | 111 | 0 | 111 | 35.44 | 1.98 | 38.15 | 92.5% |

### Stage Balance

| Worker | Profile | Batches | Success | Failed | Avg Batch(s) | Setup(s) | Add(s) | Extract(s) | Avg Extract(s) | Cleanup(s) | Avg SR Age(s) | Max SR Age(s) |
|--------|---------|---------|---------|--------|--------------|----------|--------|------------|---------------|------------|---------------|---------------|
| worker-01 | ytis-pro-worker-01 | 2 | 10 | 40 | 331.23 | 58.22 | 53.39 | 473.15 | 236.57 | 131.09 | 11.3 | 139.4 |

- stage balance skew: extract spread 0.0s; per-batch spread 0.0s; dominant worker worker-01/ytis-pro-worker-01 at 473.1s/2 batches vs worker-01/ytis-pro-worker-01 at 473.1s/2 batches; per-batch dominant worker worker-01/ytis-pro-worker-01 at 236.6s vs worker-01/ytis-pro-worker-01 at 236.6s

| Last Auth Refresh Age | Commands | Failed | Failure Rate |
|-----------------------|----------|--------|--------------|
| 20-59s | 120 | 111 | 92.5% |

- skew comparison: worker-profile spread 0.0pp vs auth-refresh spread 0.0pp; worker balance is the stronger signal

### Fetch Recovery Attribution

- browser profile roots: P:\.data\yt-is\browser\notebooklm-pro
- browser profile directories: Profile
- worker state roots: P:\packages\yt-is\.logs\sharded_lane_series\hotel_wifi_3plus3_shared_retry_canary_run26_current\smoke\a_hominidae_pro\worker_states

| Worker | Profile | Pass | Batch Index | Fetches | Status Distribution | Avg Attempts | Avg SR Age(s) | Max SR Age(s) | Max Projected Retry Age(s) | Max Projected+Margin Age(s) | Max Retry Age Margin(s) | Retry Queued | Retry Gate Reasons | Retry Queue Skipped | Cmd Total(s) | Source-List Probes | Source-List Probe(s) | YT-DLP Probe(s) | Source Validated | Source Missing |
|--------|---------|------|-------------|---------|---------------------|--------------|---------------|---------------|----------------------------|-----------------------------|-------------------------|--------------|--------------------|---------------------|--------------|--------------------|----------------------|-----------------|------------------|----------------|
| worker-01 | ytis-pro-worker-01 | primary | n/a | 74 | command_failed:43, ready:31 | 2.12 | 105.5 | 270.5 | 300.5 | 300.5 | 0.0 | 43 | ytdlp_ok:43 | none | 4296.47 | 2 | 123.21 | 79.75 | 2 | 0 |

### Default Profile Recovery

| Worker | Profile | Before Cmd | After Cmd | Before Auth | After Auth | During Cleanup | Total |
|--------|---------|------------|-----------|-------------|------------|-----------------|-------|
| worker-01 | ytis-pro-worker-01 | 0 | 0 | 0 | 0 | 0 | 92 |

### Batch Attribution

#### smoke / batch_01 / 20260603_030401

| Worker | Profile | Commands | Failed | Failure Rate | Avg Cmd(s) | Avg Ready Cmd(s) | Avg Failed Cmd(s) | Avg SR Age(s) | Max SR Age(s) | Avg Attempt | Max Attempt | Status Distribution | Auth Buckets |
|--------|---------|----------|--------|--------------|------------|------------------|-------------------|---------------|---------------|-------------|-------------|---------------------|--------------|
| worker-01 | ytis-pro-worker-01 | 62 | 57 | 91.9% | 39.75 | 2.14 | 43.05 | 71.7 | 151.2 | 1.89 | 3 | command_failed:57, ready:5 | 20-59s:62 |

#### smoke / batch_02 / 20260603_031140

| Worker | Profile | Commands | Failed | Failure Rate | Avg Cmd(s) | Avg Ready Cmd(s) | Avg Failed Cmd(s) | Avg SR Age(s) | Max SR Age(s) | Avg Attempt | Max Attempt | Status Distribution | Auth Buckets |
|--------|---------|----------|--------|--------------|------------|------------------|-------------------|---------------|---------------|-------------|-------------|---------------------|--------------|
| worker-01 | ytis-pro-worker-01 | 58 | 54 | 93.1% | 30.83 | 1.78 | 32.98 | 66.9 | 141.5 | 1.90 | 3 | command_failed:54, ready:4 | 20-59s:58 |

### Retry Queue Window

- windows: 5
- deferred/recovered/final failed: 43/0/0
- shared deferred/recovered/final failed: 43/0/0
- delay/budget: 30.0s / 30.0s
- drain ready age max: absent
- retry queue wait max/count: 0.0s / 0
- retry queue sleep elapsed total: 0.0s
### Lane: troup_hominidae_free
- aggregate VPH: 0.00
- aggregate wall: 0.0s
- aggregate startup prepare: 0.0s
- aggregate startup notebook check: 0.0s
- aggregate startup notebook create: 0.0s
- aggregate startup notebook retire: 0.0s
- aggregate startup prepare cleanup: 0.0s
- aggregate setup: 0.0s
- aggregate setup excluding add: 0.0s
- aggregate add: 0.0s
- aggregate cleanup: 0.0s
- aggregate idle wait: 0.0s
- aggregate sr_age_avg: 0.0s
- success/fail/processed: 0/0/0

| Phase | Batch | Timestamp | Workers | elapsed(s) | setup(s) | setup_excl_add(s) | extract(s) | add(s) | cleanup(s) | sr_age(s) | command_failed | ready | **Lane Bottleneck** |
|-------|-------|-----------|---------|------------|----------|-------------------|------------|--------|------------|----------|----------------|-------|----------------|
| smoke | batch_01 | 20260603_030401 | 3 | 358.0 | 24.5 | 2.0 | 162.1 | 22.5 | 5.0 | 60.4 | 0 | 10 | stage-sum-suggested:extract [extract=80% of aggregate stage sum, recovered-unknown] |

| Worker | Worker Batch Count | Succeeded | Failed |
|--------|-------|-----------|--------|
| worker-01 | 1 | 10 | 15 |
| worker-01 | 1 | 14 | 11 |

| smoke | batch_02 | 20260603_031014 | 3 | 411.7 | 44.3 | 1.8 | 191.7 | 42.5 | 5.4 | 91.7 | 0 | 14 | stage-sum-suggested:extract [extract=80% of aggregate stage sum, recovered-unknown] |

| Worker | Worker Batch Count | Succeeded | Failed |
|--------|-------|-----------|--------|
| worker-01 | 1 | 10 | 15 |
| worker-01 | 1 | 14 | 11 |

### Command Attribution

- command completions: 93
- failures: 69 (74.2%)

- browser profile roots: P:\.data\yt-is\browser\notebooklm-free
- browser profile directories: Profile 1
- worker state roots: P:\packages\yt-is\.logs\sharded_lane_series\hotel_wifi_3plus3_shared_retry_canary_run26_current\smoke\troup_hominidae_free\worker_states

| Worker | Profile | Commands | Ready | Failed | Source-Age-Cliff | Command-Failed | Avg Cmd(s) | Avg Ready Cmd(s) | Avg Failed Cmd(s) | Failure Rate |
|--------|---------|----------|-------|--------|------------------|----------------|------------|------------------|-------------------|--------------|
| worker-01 | ytis-free1-worker-01 | 93 | 24 | 69 | 0 | 69 | 32.32 | 12.91 | 39.08 | 74.2% |

### Stage Balance

| Worker | Profile | Batches | Success | Failed | Avg Batch(s) | Setup(s) | Add(s) | Extract(s) | Avg Extract(s) | Cleanup(s) | Avg SR Age(s) | Max SR Age(s) |
|--------|---------|---------|---------|--------|--------------|----------|--------|------------|---------------|------------|---------------|---------------|
| worker-01 | ytis-free1-worker-01 | 2 | 24 | 26 | 216.51 | 68.78 | 64.98 | 353.81 | 176.91 | 10.43 | 37.8 | 179.9 |

- stage balance skew: extract spread 0.0s; per-batch spread 0.0s; dominant worker worker-01/ytis-free1-worker-01 at 353.8s/2 batches vs worker-01/ytis-free1-worker-01 at 353.8s/2 batches; per-batch dominant worker worker-01/ytis-free1-worker-01 at 176.9s vs worker-01/ytis-free1-worker-01 at 176.9s

| Last Auth Refresh Age | Commands | Failed | Failure Rate |
|-----------------------|----------|--------|--------------|
| 20-59s | 93 | 69 | 74.2% |

- skew comparison: worker-profile spread 0.0pp vs auth-refresh spread 0.0pp; worker balance is the stronger signal

### Fetch Recovery Attribution

- browser profile roots: P:\.data\yt-is\browser\notebooklm-free
- browser profile directories: Profile 1
- worker state roots: P:\packages\yt-is\.logs\sharded_lane_series\hotel_wifi_3plus3_shared_retry_canary_run26_current\smoke\troup_hominidae_free\worker_states

| Worker | Profile | Pass | Batch Index | Fetches | Status Distribution | Avg Attempts | Avg SR Age(s) | Max SR Age(s) | Max Projected Retry Age(s) | Max Projected+Margin Age(s) | Max Retry Age Margin(s) | Retry Queued | Retry Gate Reasons | Retry Queue Skipped | Cmd Total(s) | Source-List Probes | Source-List Probe(s) | YT-DLP Probe(s) | Source Validated | Source Missing |
|--------|---------|------|-------------|---------|---------------------|--------------|---------------|---------------|----------------------------|-----------------------------|-------------------------|--------------|--------------------|---------------------|--------------|--------------------|----------------------|-----------------|------------------|----------------|
| worker-01 | ytis-free1-worker-01 | primary | n/a | 66 | command_failed:34, ready:32 | 2.03 | 93.7 | 192.3 | 222.3 | 222.3 | 0.0 | 34 | ytdlp_ok:34 | none | 3540.18 | 2 | 213.28 | 63.07 | 2 | 0 |

### Default Profile Recovery

| Worker | Profile | Before Cmd | After Cmd | Before Auth | After Auth | During Cleanup | Total |
|--------|---------|------------|-----------|-------------|------------|-----------------|-------|
| worker-01 | ytis-free1-worker-01 | 0 | 0 | 0 | 0 | 0 | 105 |

### Batch Attribution

#### smoke / batch_01 / 20260603_030401

| Worker | Profile | Commands | Failed | Failure Rate | Avg Cmd(s) | Avg Ready Cmd(s) | Avg Failed Cmd(s) | Avg SR Age(s) | Max SR Age(s) | Avg Attempt | Max Attempt | Status Distribution | Auth Buckets |
|--------|---------|----------|--------|--------------|------------|------------------|-------------------|---------------|---------------|-------------|-------------|---------------------|--------------|
| worker-01 | ytis-free1-worker-01 | 40 | 30 | 75.0% | 35.50 | 19.70 | 40.77 | 33.5 | 129.8 | 1.55 | 3 | command_failed:30, ready:10 | 20-59s:40 |

#### smoke / batch_02 / 20260603_031014

| Worker | Profile | Commands | Failed | Failure Rate | Avg Cmd(s) | Avg Ready Cmd(s) | Avg Failed Cmd(s) | Avg SR Age(s) | Max SR Age(s) | Avg Attempt | Max Attempt | Status Distribution | Auth Buckets |
|--------|---------|----------|--------|--------------|------------|------------------|-------------------|---------------|---------------|-------------|-------------|---------------------|--------------|
| worker-01 | ytis-free1-worker-01 | 53 | 39 | 73.6% | 29.93 | 8.07 | 37.78 | 49.0 | 191.2 | 2.02 | 4 | command_failed:39, ready:14 | 20-59s:53 |

### Retry Queue Window

- windows: 4
- deferred/recovered/final failed: 34/0/0
- shared deferred/recovered/final failed: 34/0/0
- delay/budget: 30.0s / 30.0s
- drain ready age max: absent
- retry queue wait max/count: 0.0s / 0
- retry queue sleep elapsed total: 0.0s

