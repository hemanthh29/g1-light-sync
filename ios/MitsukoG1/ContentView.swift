import SwiftUI

private enum Palette {
    static let bg = Color(red: 0.027, green: 0.063, blue: 0.055)
    static let card = Color(red: 0.067, green: 0.122, blue: 0.106)
    static let card2 = Color(red: 0.09, green: 0.157, blue: 0.137)
    static let green = Color(red: 0.275, green: 0.839, blue: 0.667)
    static let text = Color(red: 0.95, green: 0.97, blue: 0.95)
    static let muted = Color(red: 0.61, green: 0.70, blue: 0.65)
}

struct ContentView: View {
    @StateObject private var model = CompanionModel()
    @State private var brightness = 70.0
    @State private var pairCode = ""
    @State private var showCode = false

    private struct PaletteColor: Identifiable {
        let name: String
        let rgb: [Int]
        var id: String { name }
    }
    private let colors: [PaletteColor] = [
        PaletteColor(name: "Mint", rgb: [70, 214, 170]), PaletteColor(name: "Blue", rgb: [126, 167, 255]),
        PaletteColor(name: "Violet", rgb: [196, 155, 255]), PaletteColor(name: "Coral", rgb: [255, 138, 112]),
        PaletteColor(name: "Amber", rgb: [255, 211, 109]), PaletteColor(name: "Soft white", rgb: [244, 244, 240])
    ]

    var body: some View {
        ZStack {
            Palette.bg.ignoresSafeArea()
            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 22) {
                    header
                    VStack(alignment: .leading, spacing: 7) {
                        Text("LIGHT,\nFROM YOUR PHONE.")
                            .font(.system(size: 31, weight: .bold, design: .rounded))
                            .tracking(-0.6)
                            .foregroundStyle(Palette.text)
                        Text("Control your G1 over the same Wi-Fi.")
                            .font(.subheadline)
                            .foregroundStyle(Palette.muted)
                    }
                    connectionCard
                    nowPlaying
                    sectionTitle("QUICK MODES", subtitle: "Pick the mood for the room.")
                    HStack(spacing: 12) {
                        modeTile(icon: "viewfinder", title: "Screen sync", subtitle: "Match your display") {
                            Task { await model.send("screen") }
                        }
                        musicWorkInProgressTile
                    }
                    brightnessCard
                    colorSection
                    effectsSection
                    profilesSection
                    Text("Keep G1 Light Sync open on the laptop. Your iPhone and laptop must be on the same Wi-Fi.")
                        .font(.footnote)
                        .multilineTextAlignment(.center)
                        .foregroundStyle(Palette.muted)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 2)
                        .padding(.bottom, 16)
                }
                .padding(.horizontal, 20)
                .padding(.top, 12)
            }
        }
        .sheet(isPresented: $model.showPairing) { pairingSheet }
        .alert("G1 Light Sync", isPresented: $showCode) {
            Button("OK", role: .cancel) { }
        } message: {
            Text(model.message)
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Text("G1")
                .font(.system(size: 15, weight: .bold, design: .rounded))
                .foregroundStyle(Palette.bg)
                .frame(width: 44, height: 44)
                .background(Palette.green, in: RoundedRectangle(cornerRadius: 14))
            VStack(alignment: .leading, spacing: 2) {
                Text("G1 LIGHT SYNC").font(.caption.weight(.bold)).tracking(1.1).foregroundStyle(Palette.text)
                Text("UNOFFICIAL PHANTOM G1 COMPANION").font(.system(size: 8, weight: .semibold)).tracking(0.6).foregroundStyle(Palette.muted)
            }
            Spacer()
            Button {
                Task { await model.refresh() }
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Palette.text)
                    .frame(width: 44, height: 44)
                    .background(Palette.card, in: Circle())
            }
            .accessibilityLabel("Refresh connection")
        }
    }

    private var connectionCard: some View {
        HStack(spacing: 11) {
            Circle().fill(model.connected ? Palette.green : (model.laptopFound ? .orange : Palette.muted))
                .frame(width: 9, height: 9)
            VStack(alignment: .leading, spacing: 4) {
                Text(model.connected ? "Laptop linked" : (model.laptopFound ? "Laptop found" : "Searching for laptop"))
                    .font(.subheadline.weight(.semibold)).foregroundStyle(Palette.text)
                Text(model.message)
                    .font(.caption).foregroundStyle(Palette.muted).lineLimit(2)
            }
            Spacer(minLength: 4)
            if model.needsPairing && model.laptopFound {
                Button("Pair") { model.showPairing = true }
                    .font(.caption.weight(.bold))
                    .foregroundStyle(Palette.bg)
                    .padding(.horizontal, 14).padding(.vertical, 9)
                    .background(Palette.green, in: Capsule())
            }
        }
        .padding(16)
        .background(Palette.card, in: RoundedRectangle(cornerRadius: 20))
    }

    private var nowPlaying: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("NOW PLAYING").font(.caption2.weight(.bold)).tracking(1.5).foregroundStyle(Palette.green)
            Text(modeName).font(.system(size: 23, weight: .bold, design: .rounded)).foregroundStyle(Palette.text)
            Text("Your laptop handles the LEDs.").font(.caption).foregroundStyle(Palette.muted)
            actionButton("Turn LEDs off", filled: true) { Task { await model.send("off") } }
                .padding(.top, 7)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LinearGradient(colors: [Color(red: 0.10, green: 0.27, blue: 0.21), Palette.card2], startPoint: .topLeading, endPoint: .bottomTrailing), in: RoundedRectangle(cornerRadius: 23))
    }

    private var modeName: String {
        switch model.mode {
        case "screen": return "Screen sync is on"
        case "music": return "Music reactive is on"
        case "animate": return "Lighting effect is on"
        default:
            switch model.lightState["mode"] as? String {
            case "off": return "LEDs are off"
            case "solid":
                if let rgb = model.lightState["color"] as? [Int], rgb.count == 3 {
                    return String(format: "Solid color · #%02X%02X%02X", rgb[0], rgb[1], rgb[2])
                }
                return "Solid lighting is on"
            case "effect":
                let id = model.lightState["effect_id"] as? Int ?? 0
                let number = (model.lightState["effect_param"] as? Int ?? 0) + 1
                let effect = id == 2 ? "Dynamic" : id == 3 ? "Pickup" : "Effect"
                return "\(effect) \(number) is on"
            case "animate":
                return "\((model.lightState["animation"] as? String ?? "Lighting effect").replacingOccurrences(of: "_", with: " ").capitalized) is on"
            default: return "Ready to sync"
            }
        }
    }

    private var musicWorkInProgressTile: some View {
        VStack(alignment: .leading, spacing: 9) {
            Image(systemName: "waveform").font(.system(size: 23, weight: .bold)).foregroundStyle(Palette.muted)
            Text("Music reactive · WIP").font(.system(size: 16, weight: .bold)).foregroundStyle(Palette.text)
            Text("Temporarily paused while this feature is redesigned").font(.caption).foregroundStyle(Palette.muted)
        }
        .frame(maxWidth: .infinity, minHeight: 145, alignment: .leading)
        .padding(16)
        .background(Palette.card, in: RoundedRectangle(cornerRadius: 20))
        .opacity(0.6)
        .accessibilityElement(children: .combine)
    }

    private var brightnessCard: some View {
        VStack(alignment: .leading, spacing: 11) {
            sectionTitle("BRIGHTNESS", subtitle: "Set the strip intensity.")
            HStack {
                Text("Brightness").font(.subheadline.weight(.semibold)).foregroundStyle(Palette.text)
                Spacer()
                Text("\(Int(brightness))%").font(.subheadline.weight(.bold)).foregroundStyle(Palette.green)
            }
            Slider(value: $brightness, in: 5...100, step: 1).tint(Palette.green)
            Button {
                Task { await model.send("brightness", fields: ["value": Int((brightness * 2.55).rounded())]) }
            } label: {
                Text("Apply brightness").font(.caption.weight(.bold)).foregroundStyle(Palette.text)
                    .frame(maxWidth: .infinity).padding(.vertical, 11)
                    .background(Palette.card2, in: RoundedRectangle(cornerRadius: 12))
            }
        }
        .padding(16).background(Palette.card, in: RoundedRectangle(cornerRadius: 20))
    }

    private var colorSection: some View {
        VStack(alignment: .leading, spacing: 11) {
            sectionTitle("COLOR", subtitle: "A quick color, whenever you want it.")
            HStack(spacing: 0) {
                ForEach(colors) { color in
                    Button {
                        Task { await model.send("color", fields: ["rgb": color.rgb]) }
                    } label: {
                        Circle().fill(Color(red: Double(color.rgb[0]) / 255, green: Double(color.rgb[1]) / 255, blue: Double(color.rgb[2]) / 255))
                            .frame(width: 38, height: 38)
                            .overlay(Circle().stroke(Palette.card2, lineWidth: 2))
                            .frame(maxWidth: .infinity)
                    }
                    .accessibilityLabel("Set \(color.name)")
                }
            }
            .padding(13).background(Palette.card, in: RoundedRectangle(cornerRadius: 18))
        }
    }

    private var effectsSection: some View {
        VStack(alignment: .leading, spacing: 11) {
            sectionTitle("LIGHTING EFFECTS", subtitle: "14 built-in presets plus five app effects.")
            effectPresetGrid("Dynamic · built in", effectID: 2)
            effectPresetGrid("Pickup · sound reactive · WIP", effectID: 3)
            Text("APP EFFECTS").font(.caption.weight(.bold)).tracking(1.2).foregroundStyle(Palette.green)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                ForEach(["Color Flow", "Aurora", "Breathe", "Comet", "Ember"], id: \.self) { name in
                    actionButton(name, filled: false) {
                        let id = name.lowercased().replacingOccurrences(of: " ", with: "_")
                        Task { await model.send("animate", fields: ["name": id, "color": "4c8dff"]) }
                    }
                }
            }
        }
    }

    private func effectPresetGrid(_ title: String, effectID: Int) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Palette.text)
            LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 4), spacing: 7) {
                ForEach(1...7, id: \.self) { number in
                    actionButton("\(effectID == 2 ? "Dynamic" : "Pickup") \(number)", filled: false) {
                        Task { await model.send("effect", fields: ["id": effectID, "param": number - 1]) }
                    }
                    .disabled(effectID == 3)
                    .opacity(effectID == 3 ? 0.5 : 1)
                }
            }
        }
    }

    private var profilesSection: some View {
        VStack(alignment: .leading, spacing: 11) {
            sectionTitle("YOUR PROFILES", subtitle: "Saved on the laptop.")
            if model.profiles.isEmpty {
                Text("Profiles will appear here when available.").font(.caption).foregroundStyle(Palette.muted)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(model.profiles, id: \.self) { profile in
                            Button(profile) { Task { await model.send("screen", fields: ["profile": profile]) } }
                                .font(.caption.weight(.semibold)).foregroundStyle(Palette.text)
                                .padding(.horizontal, 14).padding(.vertical, 11)
                                .background(Palette.card, in: Capsule())
                        }
                    }
                }
            }
        }
    }

    private var pairingSheet: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                Image(systemName: "wifi")
                    .font(.system(size: 32, weight: .semibold)).foregroundStyle(Palette.green)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("Pair with your laptop")
                    .font(.title2.weight(.bold)).foregroundStyle(Palette.text)
                Text("On the laptop, open G1 Light Sync → Phone Wi-Fi setup. Enter its one-time code below. After pairing, this iPhone will find it automatically on your Wi-Fi.")
                    .font(.subheadline).foregroundStyle(Palette.muted)
                TextField("8-digit pairing code", text: $pairCode)
                    .keyboardType(.numberPad)
                    .textContentType(.oneTimeCode)
                    .font(.system(size: 22, weight: .semibold, design: .monospaced))
                    .multilineTextAlignment(.center)
                    .padding(15)
                    .background(Palette.card, in: RoundedRectangle(cornerRadius: 14))
                if !model.message.isEmpty && model.message.contains("code") {
                    Text(model.message).font(.caption).foregroundStyle(.orange)
                }
                Button {
                    Task {
                        let ok = await model.pair(code: pairCode.trimmingCharacters(in: .whitespacesAndNewlines))
                        if ok { pairCode = "" }
                    }
                } label: {
                    HStack { if model.isWorking { ProgressView().tint(Palette.bg) }; Text("Connect iPhone") }
                        .font(.headline).foregroundStyle(Palette.bg).frame(maxWidth: .infinity).padding(.vertical, 15)
                        .background(Palette.green, in: RoundedRectangle(cornerRadius: 15))
                }
                .disabled(model.isWorking || pairCode.count != 8)
                .opacity(pairCode.count == 8 ? 1 : 0.55)
                Spacer()
            }
            .padding(22)
            .background(Palette.bg.ignoresSafeArea())
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Close") { model.showPairing = false }.foregroundStyle(Palette.green)
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private func sectionTitle(_ title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption.weight(.bold)).tracking(1.4).foregroundStyle(Palette.green)
            Text(subtitle).font(.caption).foregroundStyle(Palette.muted)
        }
    }

    private func modeTile(icon: String, title: String, subtitle: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                Image(systemName: icon).font(.system(size: 22, weight: .semibold)).foregroundStyle(Palette.green)
                Text(title).font(.system(size: 16, weight: .bold, design: .rounded)).foregroundStyle(Palette.text)
                Text(subtitle).font(.caption2).foregroundStyle(Palette.muted)
            }
            .frame(maxWidth: .infinity, minHeight: 112, alignment: .leading)
            .padding(15)
            .background(Palette.card, in: RoundedRectangle(cornerRadius: 19))
        }
        .buttonStyle(.plain)
    }

    private func actionButton(_ title: String, filled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.subheadline.weight(.bold))
                .foregroundStyle(filled ? Palette.bg : Palette.text)
                .lineLimit(1).minimumScaleFactor(0.82)
                .frame(maxWidth: .infinity).padding(.horizontal, 8).padding(.vertical, 13)
                .background(filled ? Palette.green : Palette.card2, in: RoundedRectangle(cornerRadius: 13))
        }
        .buttonStyle(.plain)
    }
}
