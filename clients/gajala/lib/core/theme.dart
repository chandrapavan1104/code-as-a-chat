import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Brand + semantic colors — identical in light and dark.
class GajalaColors {
  static const accent = Color(0xFF2EA6FF);
  static const accentDim = Color(0xFF1D6FB8);
  static const userBubble = Color(0xFF2B5278);
  static const ok = Color(0xFF4BB34B);
  static const danger = Color(0xFFE0533D);
  static const warn = Color(0xFFE3A008);
}

/// Adaptive surface/text colors as a ThemeExtension → read via `context.pal`.
class Pal extends ThemeExtension<Pal> {
  final Color bg, surface, surfaceAlt, text, textDim, botBubble, border;
  const Pal({required this.bg, required this.surface, required this.surfaceAlt,
    required this.text, required this.textDim, required this.botBubble, required this.border});

  static const dark = Pal(
    bg: Color(0xFF0E1621), surface: Color(0xFF17212B), surfaceAlt: Color(0xFF1C2733),
    text: Color(0xFFE9EDF0), textDim: Color(0xFF8A99A8), botBubble: Color(0xFF1C2733),
    border: Color(0xFF0A1119));
  static const light = Pal(
    bg: Color(0xFFF1F4F7), surface: Color(0xFFFFFFFF), surfaceAlt: Color(0xFFEAEFF4),
    text: Color(0xFF13202B), textDim: Color(0xFF60707E), botBubble: Color(0xFFFFFFFF),
    border: Color(0xFFD7DEE5));

  @override
  Pal copyWith({Color? bg, Color? surface, Color? surfaceAlt, Color? text,
      Color? textDim, Color? botBubble, Color? border}) => Pal(
        bg: bg ?? this.bg, surface: surface ?? this.surface,
        surfaceAlt: surfaceAlt ?? this.surfaceAlt, text: text ?? this.text,
        textDim: textDim ?? this.textDim, botBubble: botBubble ?? this.botBubble,
        border: border ?? this.border);
  @override
  Pal lerp(ThemeExtension<Pal>? other, double t) => other is Pal ? other : this;
}

extension PalContext on BuildContext {
  Pal get pal => Theme.of(this).extension<Pal>() ?? Pal.dark;
}

ThemeData buildTheme(Brightness brightness) {
  final isDark = brightness == Brightness.dark;
  final pal = isDark ? Pal.dark : Pal.light;
  final scheme = ColorScheme.fromSeed(
    seedColor: GajalaColors.accent, brightness: brightness,
  ).copyWith(surface: pal.surface, primary: GajalaColors.accent);

  final base = ThemeData(useMaterial3: true, colorScheme: scheme, brightness: brightness);
  return base.copyWith(
    scaffoldBackgroundColor: pal.bg,
    extensions: [pal],
    textTheme: GoogleFonts.interTextTheme(base.textTheme)
        .apply(bodyColor: pal.text, displayColor: pal.text),
    appBarTheme: AppBarTheme(
        backgroundColor: pal.surface, foregroundColor: pal.text,
        elevation: 0, centerTitle: false),
    cardTheme: CardThemeData(color: pal.surface, elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18))),
    inputDecorationTheme: InputDecorationTheme(
        filled: true, fillColor: pal.surfaceAlt,
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
        hintStyle: TextStyle(color: pal.textDim)),
    filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
            backgroundColor: GajalaColors.accent, foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(vertical: 16),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)))),
  );
}
