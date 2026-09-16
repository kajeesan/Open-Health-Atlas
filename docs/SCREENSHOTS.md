# Screenshots

## Lead image: visual demo overview

[View the full-resolution overview](assets/fictional-demo-overview-v2.png).

The README uses an unedited browser capture of the
[public fictional UI demo](https://kajeesan.com/openhealthatlas-demo/), taken
on 17 September 2026. The 1440 × 784 viewport was captured at 2× resolution,
producing a 2880 × 1568 PNG. The view was scrolled to the Muscle Balance and
Athletic Profile charts and the front/back strength-balance diagrams. Browser
tabs, the address bar and operating-system controls are outside the capture.

This is the older read-only UI snapshot already linked by the README. Its
displayed values belong to that snapshot; this image does not establish a
fresh calculation by the current engine. No chart, label, pixel or displayed
value was altered for the image. The empty mood card remains as displayed.

The 276,676-byte PNG contains only IHDR, IDAT and IEND chunks, with no text or
EXIF metadata. Its reviewed SHA-256 is:

`6c2b403d8477549bd4e2901757ad3501767fb282bd1eb68eecf5c2ae2ba7ea99`

## Current application: calculation screenshot

[View the calculation screenshot](assets/fictional-dashboard.png).

`assets/fictional-dashboard.png` is an unedited browser screenshot of the
current local development demo, captured on 16 September 2026 at 1440 × 750.
The generated display name is “Fictional demo”; no personal records or private
installation were used. The dashboard's All-time filter was selected through
its normal UI, and the analysis was allowed to finish before capture.

The Green-day card uses the manifest-verified `green-days-actionable-v1`
fictional fixture for 17 May–30 June 2026. It shows three computed associations,
observation counts, confidence intervals, evidence references and the separate
insufficient-data limitation. The screenshot is an example of the interface
and its evidence presentation, not a health claim or a model answer.

The public browser demo is an older read-only UI snapshot and is labelled as
such. Follow [Try the local demo](TRY_DEMO.md#run-the-local-panel) to run the
current application and calculate results yourself.

The PNG contains only standard image-data chunks (IHDR, IDAT and IEND), with
no text or EXIF metadata. Its reviewed SHA-256 is:

`3f396a93f70120e599004c981a73aa52f35a3bd11c4a29a272990aa4638b7d0f`

## Asset review and future updates

The release manifest verifies both images. The privacy scanner accepts only
their reviewed binary path/hash combinations; other binary content still fails
closed. A future screenshot needs fictional-data visual review, a new versioned
filename and an explicit scanner/manifest update. Keep earlier approved
path/hash entries in the scanner because the history check still inspects
those older images.
