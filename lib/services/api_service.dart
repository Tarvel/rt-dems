import 'dart:convert';
import 'package:http/http.dart' as http;

class ApiService {
  final String baseUrl;
  final http.Client client;

  ApiService({
    this.baseUrl = 'http://localhost:8000/api/v1',
    http.Client? client,
  }) : client = client ?? http.Client();

  /// Returns sensor data map, or empty map if no data exists yet (404).
  Future<Map<String, dynamic>> getLatestSensors() async {
    try {
      final response = await client.get(Uri.parse('$baseUrl/sensors/latest/'));
      if (response.statusCode == 200) {
        return json.decode(response.body);
      } else if (response.statusCode == 404) {
        print('No sensor data yet — simulator may not have run.');
        return {};
      } else {
        throw Exception('Failed to load sensors (${response.statusCode})');
      }
    } catch (e) {
      print('Error fetching latest sensors: $e');
      return {}; // Return empty map instead of rethrowing — don't crash the app
    }
  }

  /// Returns prediction data map, or empty map if no predictions yet (404).
  Future<Map<String, dynamic>> getLatestPrediction() async {
    try {
      final response = await client.get(
        Uri.parse('$baseUrl/predictions/latest/'),
      );
      if (response.statusCode == 200) {
        return json.decode(response.body);
      } else if (response.statusCode == 404) {
        print('No prediction data yet.');
        return {};
      } else {
        throw Exception('Failed to load prediction (${response.statusCode})');
      }
    } catch (e) {
      print('Error fetching latest prediction: $e');
      return {};
    }
  }

  /// Returns relay state map, or empty map if no relay state yet (404).
  Future<Map<String, dynamic>> getCurrentRelayState() async {
    try {
      final response = await client.get(Uri.parse('$baseUrl/relays/current/'));
      if (response.statusCode == 200) {
        return json.decode(response.body);
      } else if (response.statusCode == 404) {
        print('No relay state recorded yet.');
        return {};
      } else {
        throw Exception('Failed to load relay state (${response.statusCode})');
      }
    } catch (e) {
      print('Error fetching current relay state: $e');
      return {};
    }
  }

  Future<List<dynamic>> getSensorHistory() async {
    try {
      final response = await client.get(Uri.parse('$baseUrl/sensors/'));
      if (response.statusCode == 200) {
        final body = json.decode(response.body);
        // DRF pagination wraps results in a 'results' key
        if (body is Map && body.containsKey('results')) {
          return body['results'];
        }
        return body is List ? body : [];
      }
      return [];
    } catch (e) {
      print('Error fetching sensor history: $e');
      return [];
    }
  }

  Future<bool> updateRelay(int relayId, bool state) async {
    try {
      final response = await client.post(
        Uri.parse('$baseUrl/relays/control/'),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({'relay_id': relayId, 'state': state}),
      );
      return response.statusCode == 200;
    } catch (e) {
      print('Error updating relay: $e');
      return false;
    }
  }

  Future<bool> updateSystemMode(String mode) async {
    try {
      final response = await client.post(
        Uri.parse('$baseUrl/system/mode/'),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({'mode': mode}),
      );
      return response.statusCode == 200;
    } catch (e) {
      print('Error updating system mode: $e');
      return false;
    }
  }
}
