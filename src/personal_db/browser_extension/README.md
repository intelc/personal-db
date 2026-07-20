# Personal DB XHS Collector

This optional MV3 extension lets the `xhs` and `xhs_saved` trackers collect
their browser-only feeds without activating the user's current Chrome window.
It is a Personal DB component, independent of any other browser extension.

## Install (macOS Chrome)

Run this once for the Personal DB root that will run the trackers:

```bash
personal-db browser install
```

Then open `chrome://extensions`, enable **Developer mode**, choose **Load
unpacked**, and select the `.../browser_extension/chrome` directory printed by
the command. That directory deliberately contains only Chrome extension assets;
the Python native host is kept outside it. Reload the extension after updating
Personal DB.

The native host's socket is tied to that root at
`<root>/state/browser-collector.sock`. For a non-default root, install with
the same global root option used for syncing:

```bash
personal-db --root /path/to/personal_db browser install
```

The extension is deliberately limited to Xiaohongshu origins. It opens a
temporary unfocused collector window and always closes it after the collection
finishes. The bridge accepts only `ping` and the bundled XHS creator/saved
collector jobs; it is not a general page-fetch or browser-control interface.
