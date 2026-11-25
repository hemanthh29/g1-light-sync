# G1 Light Sync

**G1 Light Sync** is an unofficial companion for the **Mitsuko Phantom G1** LED strip. It is an independent project and is not affiliated with or endorsed by Mitsuko. The Linux desktop app connects to the strip over USB and runs screen capture, music-reactive lighting, effects, and a local phone relay. The Android and iOS companion apps control that laptop over the same Wi-Fi network, so the phone does not need to connect directly to the strip.

This public repository contains the source projects for all three platforms. Installable files are published as **separate GitHub Releases** for Linux, Android, and iOS; the newest release for each platform is listed below, and earlier versions remain available in the release history.

## Latest downloads

| Platform | Latest build | Download | Checksum |
| --- | --- | --- | --- |
| Linux | Debian package `1.0.10-34` | [`.deb` release](https://github.com/hemanthh29/g1-light-sync/releases/tag/deb-1.0.10-34) | `SHA256SUMS.txt` attached to the release |
| Android | APK `1.4` (version code 5) | [APK release](https://github.com/hemanthh29/g1-light-sync/releases/tag/android-1.4) | `SHA256SUMS.txt` attached to the release |
| iOS | IPA `1.2` (build 5) | [unsigned IPA release](https://github.com/hemanthh29/g1-light-sync/releases/tag/ios-1.2-build5) | `SHA256SUMS.txt` attached to the release |

The [full release history](https://github.com/hemanthh29/g1-light-sync/releases) keeps older builds. Release tags identify their platform: `deb-…` for Linux, `android-…` for Android, and `ios-…` for iOS.

## License

The original G1 Light Sync code and project-created assets are offered under the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0). People may use, modify, and share them for personal and other noncommercial purposes under the license terms. **Commercial use requires separate permission.** This is a source-available, noncommercial license; it is not an OSI-approved open-source license.

Third-party components keep their own license terms. See [`LICENSE`](LICENSE), `deb/source/usr/share/doc/mitsuko-g1/copyright`, and the bundled notices.

## What each app does

### Linux desktop app

The Linux app is the host and owns the USB connection to the G1. It runs on Debian-based desktops and provides:

- Screen sync that maps captured monitor colors to the strip perimeter. X11 capture is supported; on Wayland, select a monitor through the desktop screen-sharing portal.
- Music-reactive lighting with Combined, Bass, Treble, and Voice modes, sensitivity, edge sampling, smoothing, and per-zone colors.
- Built-in Dynamic and Pickup presets, plus the app-generated Color Flow, Aurora, Breathe, Comet, and Ember effects.
- Solid color, brightness, power, saved profiles, and device status controls.
- Optional desktop notification lighting with five patterns and a chosen alert color. It runs only while the strip is off or in a steady solid-color state; active effects and sync modes are left running.
- A tray icon and a phone relay that can stay on after the main window closes.

**Install:** download the `.deb` from the [Linux release](https://github.com/hemanthh29/g1-light-sync/releases/tag/deb-1.0.10-34), then install it with `sudo apt install ./g1-light-sync_1.0.10-34_all.deb`. Open **G1 Light Sync** from the desktop app menu and connect the G1 by USB. Package dependencies are declared in `deb/DEBIAN/control`.

### Android companion app

The Android app controls the strip through the Linux host. It provides:

- Screen sync and music-reactive controls, including the four music modes.
- Preset colors, brightness, stop, and lights-off controls.
- The built-in Dynamic and Pickup presets, five app-generated effects, and saved laptop profiles.
- Laptop discovery and pairing for use over the same Wi-Fi network; it can also use the USB debugging connection when configured.

**Install:** download the APK from the [Android release](https://github.com/hemanthh29/g1-light-sync/releases/tag/android-1.4). On the laptop, turn on the relay in **Phone Wi-Fi setup**. Keep both devices on the same local network, find the laptop in the app, and pair once with the code shown by the laptop.

### iOS companion app

The iPhone app uses Bonjour to find the laptop on the local network, with no IP address entry. It provides:

- Screen sync and music-reactive controls, plus Stop and Lights off.
- Brightness and color presets, built-in Dynamic and Pickup presets, five app-generated effects, and saved laptop profiles.
- Siri Shortcuts for starting Screen Sync, starting Music Reactive, and stopping the active effect.
- One-time pairing with a laptop code; the saved access token is kept in the iOS Keychain.

The published IPA is unsigned. **Install:** download it from the [iOS release](https://github.com/hemanthh29/g1-light-sync/releases/tag/ios-1.2-build5), then sign and install it with SideStore or iLoader. Allow Local Network access when iOS asks. Keep the phone and laptop on the same Wi-Fi, and enable **Phone Wi-Fi setup** on the laptop. Free Apple Account signing expires and must be refreshed periodically.

## Phone connection setup

1. Install and open G1 Light Sync on Linux, connect the G1, and open **Phone Wi-Fi setup**.
2. Enable the relay. It runs as a user service and can remain available while the desktop window is closed.
3. Connect the phone and laptop to the same local network. On iOS, allow the Local Network permission prompt.
4. Open the companion app, select the discovered laptop, and enter the one-time pairing code. The app saves its pairing token for future connections.

The relay sends control commands to the Linux app; the Linux host continues to handle the G1 USB device and screen or audio capture.

## Repository layout

```text
.
├── README.md                     Project overview, features, downloads, and setup
├── deb/                          Linux Debian package source tree
│   ├── DEBIAN/                   Package metadata and install/remove scripts
│   └── source/                   Files installed by the Debian package
├── android/                      Android Studio / Gradle companion project
│   ├── app/src/                  Android app source and resources
│   └── phone_bridge.py           USB debugging bridge used by the companion
├── ios/                          Xcode / SwiftUI companion project
│   ├── MitsukoG1/                App, UI, Bonjour discovery, and Siri Shortcuts
│   └── MitsukoG1.xcodeproj/      iOS project settings
└── .github/workflows/            Manual unsigned iOS build workflow
```

Each platform has its own source folder and its own release. APK, IPA, and DEB files are release assets rather than bundled together in one download.

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

Publish each platform independently and retain its older version tags:

- Linux: `deb-<Debian-version>` (for example, `deb-1.0.10-34`)
- Android: `android-<version>` (for example, `android-1.4`)
- iOS: `ios-<version>-build<build>` (for example, `ios-1.2-build5`)

Attach only that platform’s installable file and its `SHA256SUMS.txt` checksum to each release. Keep the release title explicit about platform and version so the newest Linux, Android, and iOS builds are easy to distinguish from historical tags.
