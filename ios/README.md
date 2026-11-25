# Mitsuko G1 for iOS

Native SwiftUI companion for controlling Mitsuko G1 over the same Wi-Fi network. It discovers the laptop through Bonjour (`_mitsukog1._tcp`), so no laptop address is typed. First-time pairing uses the laptop's one-time code; the app stores its access token in the iOS Keychain and reconnects automatically afterward.

The app includes screen sync, music reactive modes, stop and lights off, brightness, color presets, lighting effects, and saved profiles. The phone talks directly to the laptop bridge; light control remains on the laptop.

The iPhone asks for Local Network access the first time it searches for the laptop. Allow that prompt. Both devices must be on the same Wi-Fi network, and the laptop's phone relay must be on in **Phone Wi-Fi setup**. Once enabled, the relay runs independently of the desktop window. Bonjour support on the laptop is provided by Avahi.

## Build and install without owning a Mac

The `.github/workflows/ios-unsigned-build.yml` workflow builds an unsigned IPA on a GitHub Actions macOS runner. It uses no signing credentials. Run **Actions → Build unsigned iOS IPA → Run workflow** and download the `MitsukoG1-unsigned-IPA` artifact.

On the Linux laptop, install the official [iLoader](https://github.com/nab138/iloader/releases/latest) release and `usbmuxd`, connect the iPhone by USB, unlock it, and tap **Trust**. iLoader can sign and install an IPA using your Apple Account. This uses your Apple Account on your own computer; do not send its password to anyone. On iOS 16 and later, turn on Developer Mode. A free Apple Account signing profile expires after seven days, so use SideStore to refresh the installed app or reinstall with iLoader when it expires. SideStore requires the LocalDevVPN app while refreshing; see its [official Linux setup instructions](https://github.com/SideStore/SideStore-Docs/blob/main/docs/installation/prerequisites.mdx).

For first pairing, choose **Phone Wi-Fi setup** on the laptop to see the current code. In the iPhone app, tap **Pair** and enter that code once. If the saved pairing expires, the iPhone app clears the stale token and asks you to pair again. No IP address is entered. When installing the updated Linux package on another laptop, install the recommended `avahi-daemon` package so iOS Bonjour discovery is available.
