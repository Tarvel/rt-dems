import 'package:mqtt_client/mqtt_browser_client.dart';
import 'package:mqtt_client/mqtt_client.dart';

MqttClient createMqttClient(
  String server,
  String clientIdentifier, {
  int? port,
}) {
  // MqttBrowserClient requires ws:// or wss:// prefix.
  // Mosquitto serves WebSockets at the root path — no /mqtt suffix needed.
  String wsUrl = server;
  if (!wsUrl.startsWith('ws://') && !wsUrl.startsWith('wss://')) {
    wsUrl = 'ws://$wsUrl';
  }
  if (port != null) {
    wsUrl = '$wsUrl:$port/mqtt';
  } else {
    wsUrl = '$wsUrl/mqtt';
  }
  
  final client = MqttBrowserClient(wsUrl, clientIdentifier);
  if (port != null) client.port = port;
  client.websocketProtocols = MqttClientConstants.protocolsSingleDefault;
  return client;
}
