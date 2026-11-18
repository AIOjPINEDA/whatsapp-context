// wa-transcribe: on-device speech-to-text for voice notes WAExport copied out of WhatsApp.
//
//   wa-transcribe <locale> <file>...    one JSON line per file: {file, seconds, text, confidence} or {file, error}
//   wa-transcribe --install <locale>    asks macOS to download that language's speech model (from Apple)
//
// Uses Apple's SpeechAnalyzer/SpeechTranscriber (macOS 26+): inference runs in a system service on this
// Mac. It has no Full Disk Access and opens only the files it is given; it never reads WhatsApp's data.

import AVFoundation
import Foundation
import Speech

setvbuf(stdout, nil, _IOLBF, 0)

func emit(_ object: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
       let line = String(data: data, encoding: .utf8) { print(line) }
}

@available(macOS 26, *)
func resolve(_ identifier: String) async -> Locale? {
    await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: identifier))
}

@available(macOS 26, *)
func install(_ identifier: String) async -> Int32 {
    guard let locale = await resolve(identifier) else {
        emit(["error": "language not supported: \(identifier)"]); return 2
    }
    let module = SpeechTranscriber(locale: locale, preset: .transcription)
    do {
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [module]) {
            try await request.downloadAndInstall()
        }
        emit(["installed": locale.identifier]); return 0
    } catch {
        emit(["error": "could not install \(locale.identifier): \(error)"]); return 1
    }
}

@available(macOS 26, *)
func transcribe(_ identifier: String, _ paths: [String]) async -> Int32 {
    guard SpeechTranscriber.isAvailable else { emit(["error": "speech transcription is not available on this Mac"]); return 2 }
    guard let locale = await resolve(identifier) else { emit(["error": "language not supported: \(identifier)"]); return 2 }
    let installed = await SpeechTranscriber.installedLocales.map(\.identifier)
    guard installed.contains(locale.identifier) else {
        emit(["error": "speech model for \(locale.identifier) is not installed: run wa transcribe --install"]); return 3
    }
    for path in paths {
        let name = URL(fileURLWithPath: path).lastPathComponent
        do {
            let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
            let seconds = Double(file.length) / file.processingFormat.sampleRate
            let transcriber = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [],
                                                attributeOptions: [.transcriptionConfidence])
            let collect = Task { () -> (String, [Double]) in
                var text = "", confidences: [Double] = []
                for try await result in transcriber.results where result.isFinal {
                    text += String(result.text.characters)
                    for run in result.text.runs { if let c = run.transcriptionConfidence { confidences.append(c) } }
                }
                return (text, confidences)
            }
            let analyzer = SpeechAnalyzer(modules: [transcriber])
            if let last = try await analyzer.analyzeSequence(from: file) {
                try await analyzer.finalizeAndFinish(through: last)
            } else {
                await analyzer.cancelAndFinishNow()
            }
            let (text, confidences) = try await collect.value
            let mean = confidences.isEmpty ? nil : confidences.reduce(0, +) / Double(confidences.count)
            emit(["file": name, "seconds": (seconds * 10).rounded() / 10, "locale": locale.identifier,
                  "text": text.trimmingCharacters(in: .whitespacesAndNewlines),
                  "confidence": mean.map { ($0 * 100).rounded() / 100 } ?? NSNull()])
        } catch {
            emit(["file": name, "error": "\(error)"])
        }
    }
    return 0
}

var args = Array(CommandLine.arguments.dropFirst())
if #available(macOS 26, *) {
    if args.first == "--install", args.count == 2 {
        exit(await install(args[1]))
    } else if args.count >= 1, !args[0].hasPrefix("-") {
        exit(await transcribe(args.removeFirst(), args))
    }
    emit(["error": "usage: wa-transcribe <locale> <file>... | wa-transcribe --install <locale>"]); exit(2)
} else {
    emit(["error": "voice-note transcription needs macOS 26 or later"]); exit(2)
}
