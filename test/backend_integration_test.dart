import 'package:flutter_test/flutter_test.dart';
import 'package:energy_management_system/services/api_service.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'dart:convert';

void main() {
  group('ApiService Tests', () {
    test('getLatestSensors returns data on 200', () async {
      final mockClient = MockClient((request) async {
        return http.Response(
          json.encode({
            'temperature': 22.5,
            'humidity': 45.0,
            'occupancy': 1,
            'voltage': 230.0,
            'current': 5.2,
            'battery_level': 88.0,
          }),
          200,
        );
      });

      final api = ApiService(client: mockClient);
      final data = await api.getLatestSensors();

      expect(data['temperature'], 22.5);
      expect(data['humidity'], 45.0);
      expect(data['occupancy'], 1);
      expect(data['voltage'], 230.0);
    });

    test('getSensorHistory returns list on 200', () async {
      final mockClient = MockClient((request) async {
        return http.Response(
          json.encode([
            {'temperature': 20.0, 'current': 1.0},
            {'temperature': 21.0, 'current': 1.5},
          ]),
          200,
        );
      });

      final api = ApiService(client: mockClient);
      final data = await api.getSensorHistory();

      expect(data.length, 2);
      expect(data[0]['temperature'], 20.0);
    });

    test('getLatestPrediction returns parsed ML data on 200', () async {
      final mockClient = MockClient((request) async {
        return http.Response(
          json.encode({
            'mean_prediction_kw': 1.25,
            'upper_bound_kw': 1.45,
            'predicted_power_w': 1250.0,
          }),
          200,
        );
      });

      final api = ApiService(client: mockClient);
      final data = await api.getLatestPrediction();

      expect(data['mean_prediction_kw'], 1.25);
      expect(data['upper_bound_kw'], 1.45);
    });
  });
}
