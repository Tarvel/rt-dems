import 'package:mqtt_client/mqtt_client.dart';

MqttClient createMqttClient(
  String server,
  String clientIdentifier, {
  int? port,
}) {
  throw UnsupportedError('Cannot create a client without dart:io or dart:html');
}
