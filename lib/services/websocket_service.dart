import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:web_socket_channel/web_socket_channel.dart';

class WebSocketService {
  final String url;
  WebSocketChannel? _channel;

  final _dataController = StreamController<Map<String, dynamic>>.broadcast();
  Stream<Map<String, dynamic>> get dataStream => _dataController.stream;

  bool _isConnected = false;
  Timer? _reconnectTimer;

  WebSocketService({required this.url});

  void connect() {
    try {
      _channel = WebSocketChannel.connect(Uri.parse(url));
      _isConnected = true;
      print('WebSocket connected to $url');

      _channel!.stream.listen(
        (message) {
          try {
            final data = json.decode(message as String);
            if (data is Map<String, dynamic>) {
              _dataController.add(data);
            }
          } catch (e) {
            print('WebSocket parse error: $e');
          }
        },
        onError: (error) {
          print('WebSocket error: $error');
          _scheduleReconnect();
        },
        onDone: () {
          print('WebSocket closed — reconnecting...');
          _scheduleReconnect();
        },
        cancelOnError: false,
      );
    } catch (e) {
      print('WebSocket connect failed: $e');
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    _isConnected = false;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 5), connect);
  }

  void dispose() {
    _reconnectTimer?.cancel();
    _channel?.sink.close();
    _dataController.close();
  }
}
