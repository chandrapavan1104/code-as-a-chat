import 'api.dart';

/// Structured read and terminate operations for the Mac's listening sockets.
extension PortsApi on GajalaApi {
  Future<Map<String, dynamic>> listeningPorts() async =>
      getSkillData('/api/ports');

  Future<Map<String, dynamic>> listeningPort(int port) async =>
      getSkillData('/api/ports/$port');

  Future<Map<String, dynamic>> terminateListener(
    int port,
    Map<String, dynamic> process,
  ) async => Map<String, dynamic>.from(
    await postSkillAction('/api/ports/$port/terminate', {
          'pid': process['pid'],
          'command': process['command'],
          'address': process['address'],
          'started_at': process['started_at'],
        })
        as Map,
  );
}
