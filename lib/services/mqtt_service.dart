import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:mqtt_client/mqtt_client.dart';
import 'mqtt_setup_stub.dart'
    if (dart.library.io) 'mqtt_setup_native.dart'
    if (dart.library.html) 'mqtt_setup_web.dart';

class MqttService {
  final String server;
  final int port;
  final String clientIdentifier;
  late MqttClient client;

  // room/data/averaged — 5-minute averaged sensor telemetry
  final _dataStreamController =
      StreamController<Map<String, dynamic>>.broadcast();
  Stream<Map<String, dynamic>> get dataStream => _dataStreamController.stream;

  // room/ml/predictions — real-time ML prediction payloads
  final _mlStreamController =
      StreamController<Map<String, dynamic>>.broadcast();
  Stream<Map<String, dynamic>> get mlStream => _mlStreamController.stream;

  // room/relays/state — relay decisions and battery lag updates
  final _relayStreamController =
      StreamController<Map<String, dynamic>>.broadcast();
  Stream<Map<String, dynamic>> get relayStream => _relayStreamController.stream;

  MqttService({
    required this.server,
    int? port,
    this.clientIdentifier = 'flutter_client',
  }) : port = port ?? (kIsWeb ? 9001 : 1883) {
    client = createMqttClient(server, clientIdentifier, port: this.port);
    client.keepAlivePeriod = 20;
    client.onDisconnected = _onDisconnected;
    client.onConnected = _onConnected;
    client.onSubscribed = _onSubscribed;
  }

  Future<void> connect() async {
    try {
      await client.connect();
    } on Exception catch (e) {
      print('MQTT client exception - $e');
      client.disconnect();
    }

    if (client.connectionStatus!.state == MqttConnectionState.connected) {
      print('MQTT client connected');
      _setupListeners();
    } else {
      print(
        'MQTT client connection failed - status is ${client.connectionStatus}',
      );
      client.disconnect();
    }
  }

  void _setupListeners() {
    client.updates!.listen((List<MqttReceivedMessage<MqttMessage?>>? c) {
      final recMess = c![0].payload as MqttPublishMessage;
      final pt = MqttPublishPayload.bytesToStringAsString(
        recMess.payload.message,
      );

      print('MQTT Topic: ${c[0].topic}, payload: $pt');

      try {
        final data = json.decode(pt);
        // room/sensors — raw sensor telemetry every ~5 s (carries energy_kwh for real-time load)
        // room/data/averaged — 5-minute averages from mqtt_logger
        // Both go into dataStream; _updateSensorState handles both gracefully.
        if (c[0].topic == 'room/sensors' ||
            c[0].topic == 'room/data/averaged') {
          _dataStreamController.add(data);
        } else if (c[0].topic == 'room/ml/predictions') {
          _mlStreamController.add(data);
        } else if (c[0].topic == 'room/relays/state') {
          _relayStreamController.add(data);
        }
      } catch (e) {
        print('Error decoding MQTT message: $e');
      }
    });

    client.subscribe('room/sensors', MqttQos.atLeastOnce);
    client.subscribe('room/data/averaged', MqttQos.atLeastOnce);
    client.subscribe('room/ml/predictions', MqttQos.atLeastOnce);
    client.subscribe('room/relays/state', MqttQos.atLeastOnce);
  }

  void _onConnected() {
    print('MQTT Connected');
  }

  void _onDisconnected() {
    print('MQTT Disconnected');
  }

  void _onSubscribed(String topic) {
    print('MQTT Subscribed to $topic');
  }

  void dispose() {
    client.disconnect();
    _dataStreamController.close();
    _mlStreamController.close();
    _relayStreamController.close();
  }
}
