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

  // Refined dark: near-black, slightly cool. Surfaces separate by a HAIRLINE
  // border rather than shadow, so the UI reads calm and flat (Linear/Notion).
  static const dark = Pal(
    bg: Color(0xFF0C0E12), surface: Color(0xFF14171C), surfaceAlt: Color(0xFF1A1E24),
    text: Color(0xFFE7EAEE), textDim: Color(0xFF8B949E), botBubble: Color(0xFF15181D),
    border: Color(0xFF242A32));
  static const light = Pal(
    bg: Color(0xFFFAFBFC), surface: Color(0xFFFFFFFF), surfaceAlt: Color(0xFFF2F4F7),
    text: Color(0xFF15181D), textDim: Color(0xFF6B7280), botBubble: Color(0xFFFFFFFF),
    border: Color(0xFFE3E7EC));

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
  // Tighter tracking on headings, comfortable body — a clear type hierarchy is
  // what makes a restrained palette read as "designed" rather than plain.
  final text = GoogleFonts.interTextTheme(base.textTheme)
      .apply(bodyColor: pal.text, displayColor: pal.text)
      .copyWith(
        titleLarge: GoogleFonts.inter(
            fontSize: 19, fontWeight: FontWeight.w700, letterSpacing: -0.3, color: pal.text),
        titleMedium: GoogleFonts.inter(
            fontSize: 15, fontWeight: FontWeight.w600, letterSpacing: -0.1, color: pal.text),
        bodyMedium: GoogleFonts.inter(fontSize: 14.5, height: 1.45, color: pal.text),
        bodySmall: GoogleFonts.inter(fontSize: 12.5, height: 1.4, color: pal.textDim),
        labelSmall: GoogleFonts.inter(
            fontSize: 11, fontWeight: FontWeight.w600, letterSpacing: 0.8, color: pal.textDim),
      );

  return base.copyWith(
    scaffoldBackgroundColor: pal.bg,
    extensions: [pal],
    textTheme: text,
    // The header blends into the page instead of sitting on a slab.
    appBarTheme: AppBarTheme(
        backgroundColor: pal.bg, foregroundColor: pal.text,
        elevation: 0, scrolledUnderElevation: 0, centerTitle: false,
        titleTextStyle: text.titleLarge),
    dividerTheme: DividerThemeData(color: pal.border, thickness: 1, space: 1),
    cardTheme: CardThemeData(
        color: pal.surface, elevation: 0, margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
            side: BorderSide(color: pal.border))),
    inputDecorationTheme: InputDecorationTheme(
        filled: true, fillColor: pal.surfaceAlt,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: BorderSide(color: pal.border)),
        enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: BorderSide(color: pal.border)),
        focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: const BorderSide(color: GajalaColors.accent, width: 1.4)),
        hintStyle: TextStyle(color: pal.textDim)),
    chipTheme: ChipThemeData(
        backgroundColor: pal.surfaceAlt,
        selectedColor: GajalaColors.accent.withValues(alpha: .18),
        side: BorderSide(color: pal.border),
        labelStyle: TextStyle(fontSize: 12.5, color: pal.text),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(9))),
    filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
            backgroundColor: GajalaColors.accent, foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(vertical: 15, horizontal: 18),
            textStyle: GoogleFonts.inter(fontSize: 14, fontWeight: FontWeight.w600),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(11)))),
    snackBarTheme: SnackBarThemeData(
        backgroundColor: pal.surfaceAlt,
        contentTextStyle: TextStyle(color: pal.text, fontSize: 13.5),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(11))),
  );
}
