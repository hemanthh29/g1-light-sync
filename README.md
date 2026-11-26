# G1 Light Sync

**G1 Light Sync** is an unofficial companion for the **Mitsuko Phantom G1** LED strip. It is an independent project and is not affiliated with or endorsed by Mitsuko. The Linux desktop app connects to the strip over USB and runs screen capture, effects, and a local phone relay. The Android and iOS companion apps control that laptop over the same Wi-Fi network, so the phone does not need to connect directly to the strip.

This public repository contains the source projects for all three platforms. Installable files are published as **separate GitHub Releases** for Linux, Android, and iOS. Only the current release for each platform is published; older release downloads and tags are removed when superseded.

## Latest downloads

| Platform | Current build | Download | Checksum |
| --- | --- | --- | --- |
| Linux | WIP DEB `1.0.10-43+2` | [`.deb` release](https://github.com/hemanthh29/g1-light-sync/releases/tag/deb-1.0.10-43-wip2) | SHA-256 sidecar attached |
| Android | WIP APK `1.8.3` (version code 12) | [APK release](https://github.com/hemanthh29/g1-light-sync/releases/tag/android-1.8.3-wip) | SHA-256 sidecar attached |
| iOS | WIP unsigned IPA `1.4` (build 9) | [IPA release](https://github.com/hemanthh29/g1-light-sync/releases/tag/ios-1.4-build9-wip) | SHA-256 sidecar attached |

The [releases page](https://github.com/hemanthh29/g1-light-sync/releases) contains exactly these three platform releases. Release tags identify their platform: `deb-…` for Linux, `android-…` for Android, and `ios-…` for iOS. Source code history remains in the repository.

## WIP status

The three downloads above are WIP builds. Music Reactive controls remain disabled across Linux, Android, and iOS while that feature is redesigned. Built-in Pickup presets are available on all three platforms. Screen Sync, Dynamic built-in presets, solid color, brightness and power controls, and the five app-generated effects remain available. Linux rejects Music Reactive requests from the CLI and phone relay. These are app changes only; they do not modify controller firmware. Each release includes a SHA-256 checksum sidecar.

## License

The original G1 Light Sync code and project-created assets are offered under the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0). People may use, modify, and share them for personal and other noncommercial purposes under the license terms. **Commercial use requires separate permission.** This is a source-available, noncommercial license; it is not an OSI-approved open-source license.

Third-party components keep their own license terms. See [`LICENSE`](LICENSE), `deb/usr/share/doc/mitsuko-g1/copyright`, and the bundled notices.

## What each app does

### Linux desktop app

The Linux app is the host and owns the USB connection to the G1. It runs on Debian-based desktops and provides:

- Screen sync maps captured monitor colors to the strip perimeter, with adjustable edge sampling and smoothing. X11 capture is supported; on Wayland, select a monitor through the desktop screen-sharing portal.
- Built-in Dynamic presets and app-generated Color Flow, Aurora, Breathe, Comet, and Ember effects.
- Music Reactive controls are disabled and marked WIP in the current Linux pre-release. Built-in sound-reactive Pickup presets remain available. The CLI and phone relay reject Music Reactive commands.
- Solid color, brightness, power, saved profiles, and device status controls. The live-mode stop button also turns the LEDs off.
- Optional desktop notification lighting with five patterns and a chosen alert color. It runs only while the strip is off or in a steady solid-color state; active effects and sync modes are left running.
- A tray icon and a phone relay that can stay on after the main window closes. The relay can start all five app-generated lighting effects from either mobile app.
- Connected phone names in the title card, plus live lighting status shared with the companion apps. Switching to Screen Sync or another mode on a phone updates the desktop status automatically.

**Install:** download `g1-light-sync_1.0.10-43+2_all.deb` from the [Linux release](https://github.com/hemanthh29/g1-light-sync/releases/tag/deb-1.0.10-43-wip2), then install it with `sudo apt install ./g1-light-sync_1.0.10-43+2_all.deb`. Open **G1 Light Sync** from the desktop app menu and connect the G1 by USB. Package dependencies are declared in `deb/DEBIAN/control`.

### Android companion app

The Android app controls the strip through the Linux host. It provides:

- Screen Sync controls. Music Reactive is visibly marked WIP and disabled in the current Android pre-release.
- Preset colors, brightness, and a single Turn LEDs off control in the Now Playing card.
- Built-in Dynamic and Pickup presets, five app-generated effects, and saved laptop profiles.
- Automatic laptop discovery and pairing over the same Wi-Fi, with no IP address entry; it can also use the USB debugging connection when configured. Lighting mode and steady-state status refresh automatically, and Wi-Fi reconnection retries continue if the link briefly drops.
- On Android 15 and later, the screen insets keep the app header and controls clear of the status and navigation bars.

**Install:** download the APK from the [Android release](https://github.com/hemanthh29/g1-light-sync/releases/tag/android-1.8.3-wip). On the laptop, turn on the relay in **Phone Wi-Fi setup**. Keep both devices on the same local network, choose the laptop found by the app, and pair once with the code shown by the laptop.

### iOS companion app

The iPhone app uses Bonjour to find the laptop on the local network, with no IP address entry. It provides:

- Screen Sync and a single Turn LEDs off button in the Now Playing card. Music Reactive is visibly marked WIP and disabled in the current iOS pre-release.
- Brightness and color presets, built-in Dynamic and Pickup presets, five app-generated effects, and saved laptop profiles.
- Siri Shortcuts for starting Screen Sync, stopping the active effect, and turning the LEDs off. Music Reactive shortcuts are not included in the WIP build.
- One-time pairing with a laptop code; the saved access token is kept in the iOS Keychain.
- Lighting mode and steady-state status refresh automatically while the app is open.

The published IPA is unsigned. **Install:** download it from the [iOS release](https://github.com/hemanthh29/g1-light-sync/releases/tag/ios-1.4-build9-wip), then sign and install it with SideStore or iLoader. Allow Local Network access when iOS asks. Keep the phone and laptop on the same Wi-Fi, and enable **Phone Wi-Fi setup** on the laptop. Free Apple Account signing expires and must be refreshed periodically.

## Phone connection setup

1. Install and open G1 Light Sync on Linux, connect the G1, and open **Phone Wi-Fi setup**.
2. Enable the relay. It runs as a user service and can remain available while the desktop window is closed.
3. Connect the phone and laptop to the same local network. On iOS, allow the Local Network permission prompt.
4. Open the companion app, select the discovered laptop, and enter the one-time pairing code. The app saves its pairing token for future connections.

The relay sends control commands to the Linux app; the Linux host continues to handle the G1 USB device and screen capture.

## Repository layout

```text
.
├── README.md                     Project overview, features, downloads, and setup
├── deb/                          Linux Debian package source tree
│   ├── DEBIAN/                   Package metadata and install/remove scripts
│   └── usr/                      Files installed by the Debian package
├── android/                      Android Studio / Gradle companion project
│   ├── app/src/                  Android app source and resources
│   └── phone_bridge.py           USB debugging bridge used by the companion
├── ios/                          Xcode / SwiftUI companion project
│   ├── MitsukoG1/                App, UI, Bonjour discovery, and Siri Shortcuts
│   └── MitsukoG1.xcodeproj/      iOS project settings
└── .github/workflows/            Manual Android APK and unsigned iOS build workflows
```

Each platform has its own source folder and its own release. APK, IPA, and DEB files are release assets rather than bundled together in one download. The releases page contains only the current build for each platform.

## Build from source

### Linux DEB

On a Debian-based system with `dpkg-deb` installed:

```sh
dpkg-deb --build deb g1-light-sync_VERSION_all.deb
```

### Android APK

Install a compatible JDK and Android SDK, then run:

```sh
cd android
./gradlew assembleDebug
```

The APK is written to `android/app/build/outputs/apk/debug/`.

### iOS IPA

The iOS project requires Xcode to build locally. Since the IPA is unsigned, the repository also has a manual GitHub Actions workflow: open **Actions → Build G1 Light Sync iOS IPA → Run workflow**, then download the `G1LightSync-unsigned-IPA` artifact. Signing still happens on the device owner’s computer through an iOS sideloading tool.

## Release naming

Publish each platform independently and use its own version tag:

- Linux: `deb-<Debian-version>` (for example, `deb-1.0.10-43-wip2`)
- Android: `android-<version>` (for example, `android-1.8.3-wip`)
- iOS: `ios-<version>-build<build>` (for example, `ios-1.4-build9-wip`)

Attach only that platform’s installable file and a SHA-256 checksum sidecar to each release. Keep the release title explicit about platform and version. Publish only one release per platform; remove superseded release entries and tags.
