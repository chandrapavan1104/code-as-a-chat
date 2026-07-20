// In-app updater: check the server for a newer APK, download it, and launch the
// system installer — so a fresh build lands with a tap instead of a copy-paste
// link. Version is compared by build number (versionCode), which the build skill
// stamps with a fresh epoch each build.

import 'package:open_filex/open_filex.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'api.dart';

class UpdateInfo {
  final int buildNumber;      // latest available versionCode
  final String versionName;
  final String url;           // APK download URL
  const UpdateInfo(this.buildNumber, this.versionName, this.url);
}

class UpdateService {
  /// Returns update info if the server has a newer build than this install,
  /// else null. Never throws.
  static Future<UpdateInfo?> check(GajalaApi api) async {
    try {
      final info = await PackageInfo.fromPlatform();
      final current = int.tryParse(info.buildNumber) ?? 0;
      final remote = await api.appVersion();
      final latest = (remote['build_number'] as num?)?.toInt() ?? 0;
      final url = (remote['url'] ?? '').toString();
      if (latest > current && url.isNotEmpty) {
        return UpdateInfo(latest, (remote['version_name'] ?? '').toString(), url);
      }
    } catch (_) {/* offline / no manifest — no update prompt */}
    return null;
  }

  /// Download the APK (reporting 0..1 progress) and launch the installer.
  /// Returns an error string on failure, or null on success.
  static Future<String?> downloadAndInstall(
      GajalaApi api, UpdateInfo info, void Function(double) onProgress) async {
    try {
      final dir = await getExternalStorageDirectory() ??
          await getApplicationDocumentsDirectory();
      final path = '${dir.path}/gajala-update-${info.buildNumber}.apk';
      await api.downloadFile(info.url, path, onProgress);
      final res = await OpenFilex.open(path,
          type: 'application/vnd.android.package-archive');
      if (res.type != ResultType.done) {
        return 'Could not open installer: ${res.message}';
      }
      return null;
    } catch (e) {
      return e.toString();
    }
  }
}
