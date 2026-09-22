import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';

Future<HttpServer> _server(Future<void> Function(HttpRequest) handler) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  server.listen(handler);
  return server;
}

void main() {
  late Directory temp;

  setUp(() {
    temp = Directory.systemTemp.createTempSync('gajala-download-test-');
  });

  tearDown(() {
    if (temp.existsSync()) temp.deleteSync(recursive: true);
  });

  test('sends the API token to an exact same-origin download', () async {
    String? receivedToken;
    final server = await _server((request) async {
      receivedToken = request.headers.value('X-API-Token');
      request.response
        ..statusCode = HttpStatus.ok
        ..add([1, 2, 3]);
      await request.response.close();
    });
    addTearDown(server.close);

    final api = GajalaApi('http://127.0.0.1:${server.port}', 'test-token');
    final path = '${temp.path}/same-origin.bin';
    await api.downloadFile(
      'http://127.0.0.1:${server.port}/download',
      path,
      (_) {},
    );

    expect(receivedToken, 'test-token');
    expect(File(path).readAsBytesSync(), [1, 2, 3]);
  });

  test('does not send the API token to another port', () async {
    String? receivedToken;
    final server = await _server((request) async {
      receivedToken = request.headers.value('X-API-Token');
      request.response
        ..statusCode = HttpStatus.ok
        ..add([4, 5, 6]);
      await request.response.close();
    });
    addTearDown(server.close);

    final api = GajalaApi('http://127.0.0.1:${server.port + 1}', 'test-token');
    final path = '${temp.path}/cross-origin.bin';
    await api.downloadFile(
      'http://127.0.0.1:${server.port}/download',
      path,
      (_) {},
    );

    expect(receivedToken, isNull);
    expect(File(path).readAsBytesSync(), [4, 5, 6]);
  });

  test('does not follow an authenticated redirect to another origin', () async {
    var redirected = false;
    final destination = await _server((request) async {
      redirected = true;
      request.response
        ..statusCode = HttpStatus.ok
        ..add([7, 8, 9]);
      await request.response.close();
    });
    addTearDown(destination.close);

    final source = await _server((request) async {
      request.response.statusCode = HttpStatus.found;
      request.response.headers.set(
        HttpHeaders.locationHeader,
        'http://127.0.0.1:${destination.port}/secret',
      );
      await request.response.close();
    });
    addTearDown(source.close);

    final api = GajalaApi('http://127.0.0.1:${source.port}', 'test-token');
    final path = '${temp.path}/redirect.bin';
    await expectLater(
      api.downloadFile(
        'http://127.0.0.1:${source.port}/download',
        path,
        (_) {},
      ),
      throwsA(isA<DioException>()),
    );

    expect(redirected, isFalse);
    expect(File(path).existsSync(), isFalse);
  });
}
