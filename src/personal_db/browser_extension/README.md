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

## Allow app-updated XHS collectors (Chrome 138+)

The static XHS collectors work immediately. To let Personal DB use the newer
app-updatable creator collector without reloading the extension, open the
extension's **Details** page at `chrome://extensions` and enable **Allow User
Scripts**. Chrome 150 requires this separate per-extension toggle even after
the extension has been loaded.

The native host sends only the current Personal DB app's vetted XHS connector
bundle to that User Scripts runtime. Sync requests name a logical connector and
bounded input; they cannot supply JavaScript, a file path, browser world, URL,
or headers. If the toggle is off or an older extension/runtime is installed,
the XHS tracker automatically retains the existing static collector path.

The native host's socket is tied to that root at
`<root>/state/browser-collector.sock`. For a non-default root, install with
the same global root option used for syncing:

```bash
personal-db --root /path/to/personal_db browser install
```

The extension is deliberately limited to Xiaohongshu origins. It opens a
temporary unfocused collector window and always closes it after the collection
finishes. The bridge accepts only `ping`, the bundled XHS creator/saved
collector jobs, and the logical XHS v2 creator capability; it is not a general
page-fetch or browser-control interface.
