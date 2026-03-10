import 'package:mqtt_client/mqtt_server_client.dart';
import 'package:mqtt_client/mqtt_client.dart';

MqttClient createMqttClient(
  String server,
  String clientIdentifier, {
  int? port,
}) {
  final client = MqttServerClient(server, clientIdentifier);
  if (port != null) client.port = port;
  return client;
}
