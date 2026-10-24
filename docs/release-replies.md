# Release reply queue

Replies to post after the next release, on threads that no released commit references with `#N`. Those get replies automatically. The daily-release workflow (`.github/workflows/daily-release.yml`) posts each entry below after it creates the release, then empties this list and commits that. On a night with no release, the entries wait.

Add one `##` section per thread:
- `close: yes|no`: close the thread after replying. Only use `yes` when the reporter confirmed the fix or asked for it to be closed.
- `brief:` what the reply must say. The workflow writes the comment from the brief, citing the released version only where the brief asks for it.

<!-- entries below; the workflow removes them once posted -->

## #224
close: yes
brief: Thank the reporter for testing on v2026.10.0 and for the fresh diagnostics after real probe trips, which confirm the upper and lower probes now work as separate entities. Say this is why the thread is being closed, and that they can reopen it if a probe misbehaves. Do not cite a version.

## #260
close: duplicate
brief: Thank the reporter. Say this is the same as #209: the H7105 Tower Fan 2 is not in the Govee Developer API device list, even on the same account where the Govee app shows it, so the integration has nothing to create it from. Point them to #209 for updates. Do not cite a version.

## #209
close: no
brief: Thank frozenbutt for confirming. Their diagnostics show the same thing: the H7105 appears in the account (BFF) census but not in the Developer API device list, on one account. The integration controls devices through the Developer API, so it cannot add the fan until Govee lists it there. Say the issue stays open to track that, and that #260 was closed as a duplicate of this one. Do not cite a version.

## #235
close: declined
brief: Thank StevenC4 for the whole H7175 kettle series. Say the rest of it (#229 to #234) is merged in this release, but the experimental keep-warm control is not being taken for now: it relies on frames that have not been verified on more than one kettle, and a heating appliance should only get controls whose frames are confirmed. Say the PR is being closed for that reason and that a version backed by more captures is welcome later. Do not include any frames.

## #229
close: no
brief: Thank StevenC4 for the kettle work. Say #229 to #234 are merged in this release as one series (the stale-temperature fix, the water heater and status entities, Brew mode, slot labels, the on-base sensor and the request_status service), and that #228 stays open until they confirm it works on their kettle. Cite the released version.

## #244
close: no
brief: Thank the reporter. Explain that an H5054 needs the account login (email and password, added under Configure) for its battery, online state and initial leak state; the API key alone gives very little for this sensor. Ask them to add the account login, wet the sensor's contacts briefly to trigger it, and then attach a diagnostics download (Settings > Devices & services > Govee > three-dot menu > Download diagnostics). Do not cite a version.

## #240
close: no
brief: Thank the reporter. The screenshots are hard to read, so ask for a diagnostics download instead (Settings > Devices & services > Govee > three-dot menu > Download diagnostics), taken with the H7073 switched on, so the colour capabilities the API reports for it can be checked. Do not cite a version.

## #239
close: no
brief: Thank the reporter. Ask for a diagnostics download (Settings > Devices & services > Govee > three-dot menu > Download diagnostics) with the account login enabled, taken twice: once with White mode switched on from the Govee app, and once with a normal colour, so the H7045's white mode can be told apart in what the device reports. Do not cite a version.

## #241
close: no
brief: Thank the reporter. Ask for a diagnostics download (Settings > Devices & services > Govee > three-dot menu > Download diagnostics) taken while Video Mode is running, started from the Govee app, with the account login enabled, so the H6099's own report of that mode can be captured. Do not cite a version.

## #213
close: no
brief: Thank Audiosenpai and Rusich90. Rusich90's report helps: on the R2A80 the REST DreamView toggle works and its response says the mode type. Ask Audiosenpai for a diagnostics download (Settings > Devices & services > Govee > three-dot menu > Download diagnostics) taken while DreamView runs, started from the Govee app, with the account login enabled. Also mention that the H66A0 additionally advertises a movie-mode capability, and ask whether the app's video mode matches that. Do not cite a version.

## #211
close: no
brief: Thank SamWiIIiams for the verified H60B0 frame table and RuslanMelnychenko for the working local code. Say the integration is waiting for RuslanMelnychenko's PR, and that frames from third parties are only added once they are verified on the device itself, so the PR with its tests is the way in. Do not include frames and do not cite a version.

## #261
close: no
brief: Thank the reporter. Ask for a diagnostics download (Settings > Devices & services > Govee > three-dot menu > Download diagnostics) for the H1A42, and ask which physical part of the strip lights up when segment 1 is set with govee.set_segment_color, so the segment numbering can be matched to the device. Do not cite a version.
