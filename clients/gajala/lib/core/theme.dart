import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Brand + semantic colors — identical in light and dark.
class GajalaColors {
  static const accent = Color(0xFF5B8DEF);
  static const accentDim = Color(0xFF3A6FD8);
  static const userBubble = Color(0xFF33518A);
  static const ok = Color(0xFF4CC38A);
  static const danger = Color(0xFFEF6B6B);
  static const warn = Color(0xFFF0A741);

  // A real palette, not one accent. Each feature owns a hue so the home screen
  // reads by colour at a glance instead of being a wall of identical tiles.
  static const blue = Color(0xFF5B8DEF);
  static const violet = Color(0xFFA57BF5);
  static const green = Color(0xFF4CC38A);
  static const amber = Color(0xFFF0A741);
  static const teal = Color(0xFF35B8C4);
  static const pink = Color(0xFFEF7BA8);
  static const red = Color(0xFFEF6B6B);
  static const indigo = Color(0xFF7C86F0);

  static const _byName = <String, Color>{
    'usage': amber, 'notes': green, 'projects': violet, 'ports': teal,
    'mac': blue, 'sysmon': indigo, 'reminders': pink, 'diary': violet,
    'claude': blue, 'codex': green, 'antigravity': violet,
    'sessions': teal, 'filemanager': amber, 'firebase': red,
    'context': indigo, 'fix': red, 'build': teal, 'errors': red,
    'model': violet, 'qwen': pink, 'auth': red,
  };

  /// Stable colour for a skill — falls back to a hash so new skills still get
  /// a distinct hue instead of everything defaulting to blue.
  static Color forSkill(String name) {
    final c = _byName[name];
    if (c != null) return c;
    const pool = [blue, violet, green, amber, teal, pink, indigo];
    return pool[name.hashCode.abs() % pool.length];
  }
}

/// Adaptive surface/text colors as a ThemeExtension → read via `context.pal`.
class Pal extends ThemeExtension<Pal> {
  final Color bg, surface, surfaceAlt, text, textDim, botBubble, border;
  const Pal({required this.bg, required this.surface, required this.surfaceAlt,
    required this.text, required this.textDim, required this.botBubble, required this.border});

  // Dark = soft charcoal with a blue cast (not near-black), so colour reads well
  // on it. Light = warm off-white (not glaring paper white).
  static const dark = Pal(
    bg: Color(0xFF15181F), surface: Color(0xFF1D212B), surfaceAlt: Color(0xFF262B37),
    text: Color(0xFFE8EAF0), textDim: Color(0xFF98A0AE), botBubble: Color(0xFF1F2430),
    border: Color(0xFF313847));
  static const light = Pal(
    bg: Color(0xFFF6F5F2), surface: Color(0xFFF7F6F3), surfaceAlt: Color(0xFFECEAE5),
    text: Color(0xFF1B1D22), textDim: Color(0xFF6B7280), botBubble: Color(0xFFF7F6F3),
    border: Color(0xFFE0DDD6));

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
