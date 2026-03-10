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

  final _dataStreamController =
      StreamController<Map<String, dynamic>>.broadcast();
  Stream<Map<String, dynamic>> get dataStream => _dataStreamController.stream;

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
        if (c[0].topic == 'room/data/averaged') {
          _dataStreamController.add(data);
        } else if (c[0].topic == 'room/relays/state') {
          _relayStreamController.add(data);
        }
      } catch (e) {
        print('Error decoding MQTT message: $e');
      }
    });

    client.subscribe('room/data/averaged', MqttQos.atLeastOnce);
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
    _relayStreamController.close();
  }
}
