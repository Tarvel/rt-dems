import 'dart:async';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:fl_chart/fl_chart.dart';
import 'services/api_service.dart';
import 'services/mqtt_service.dart';
import 'services/websocket_service.dart';

void main() {
  runApp(const RtDemsApp());
}

class RtDemsApp extends StatefulWidget {
  const RtDemsApp({super.key});

  @override
  State<RtDemsApp> createState() => _RtDemsAppState();
}

class _RtDemsAppState extends State<RtDemsApp> {
  ThemeMode _themeMode = ThemeMode.light;

  void _toggleTheme() {
    setState(() {
      _themeMode = _themeMode == ThemeMode.light
          ? ThemeMode.dark
          : ThemeMode.light;
    });
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'RT-DEMS Dashboard',
      debugShowCheckedModeBanner: false,
      themeMode: _themeMode,
      theme: ThemeData(
        brightness: Brightness.light,
        scaffoldBackgroundColor: const Color(0xFFF5F7FA),
        primaryColor: const Color(0xFF4CAF50),
        cardColor: Colors.white,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF4CAF50),
          surface: Colors.white,
          secondary: const Color(0xFF4CAF50),
        ),
        textTheme: GoogleFonts.manropeTextTheme(ThemeData.light().textTheme),
        appBarTheme: const AppBarTheme(
          backgroundColor: Colors.white,
          foregroundColor: Colors.black87,
          elevation: 0,
        ),
      ),
      darkTheme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF0F172A),
        primaryColor: const Color(0xFF4CAF50),
        cardColor: const Color(0xFF1E293B),
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF4CAF50),
          brightness: Brightness.dark,
          surface: const Color(0xFF1E293B),
          secondary: const Color(0xFF4CAF50),
        ),
        textTheme: GoogleFonts.manropeTextTheme(ThemeData.dark().textTheme),
        appBarTheme: const AppBarTheme(
          backgroundColor: Color(0xFF1E293B),
          foregroundColor: Colors.white,
          elevation: 0,
        ),
      ),
      home: DashboardShell(
        isDarkMode: _themeMode == ThemeMode.dark,
        onThemeToggled: _toggleTheme,
      ),
    );
  }
}

// ==========================================
// RESPONSIVE NAVIGATION SHELL
// ==========================================
class DashboardShell extends StatefulWidget {
  final bool isDarkMode;
  final VoidCallback onThemeToggled;
  const DashboardShell({
    super.key,
    required this.isDarkMode,
    required this.onThemeToggled,
  });

  @override
  State<DashboardShell> createState() => _DashboardShellState();
}

class _DashboardShellState extends State<DashboardShell> {
  int _selectedIndex = 0;

  // Shared System State
  String _currentMode = 'A'; // 'A', 'B', or 'C'
  bool _aiEnabled = true;

  // Added Real-time Data State
  double _temperature = 25.0;
  double _humidity = 50.0;
  int _occupancy = 0;
  double _voltage = 220.0;
  double _current = 0.0;
  double _batteryLevel = 100.0;
  List<double> _batteryHistory = [100.0, 100.0, 100.0];
  double _luminousIntensity = 0.0;
  double _predictedEnergy = 0.0;
  double _peakDemand = 0.0;

  late ApiService _apiService;
  late MqttService _mqttService;
  late WebSocketService _wsService;
  Timer? _pollingTimer;

  List<dynamic> _historyData = [];

  @override
  void initState() {
    super.initState();
    _apiService = ApiService();
    _mqttService = MqttService(server: 'localhost');
    _wsService = WebSocketService(url: 'ws://localhost:8765');
    _initBackend();
    // Poll REST API every 10 seconds as a fallback
    _pollingTimer = Timer.periodic(const Duration(seconds: 10), (_) async {
      final data = await _apiService.getLatestSensors();
      if (data.isNotEmpty && mounted) _updateSensorState(data);
      final prediction = await _apiService.getLatestPrediction();
      if (prediction.isNotEmpty && mounted) _updatePredictionState(prediction);
    });
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    _wsService.dispose();
    _mqttService.dispose();
    super.dispose();
  }

  Future<void> _initBackend() async {
    // Initial fetch from REST API — all methods return {} when no data exists
    final sensorData = await _apiService.getLatestSensors();
    if (sensorData.isNotEmpty) _updateSensorState(sensorData);

    final predictionData = await _apiService.getLatestPrediction();
    if (predictionData.isNotEmpty) _updatePredictionState(predictionData);

    final relayData = await _apiService.getCurrentRelayState();
    if (relayData.isNotEmpty) _updateRelayState(relayData);

    final history = await _apiService.getSensorHistory();
    if (history.isNotEmpty) {
      setState(() {
        _historyData = history;
      });
    }

    // Connect via WebSocket (Django Channels) for live updates
    _wsService.connect();
    _wsService.dataStream.listen((data) {
      if (mounted) _updateSensorState(data);
    });

    // Connect to MQTT on native platforms only
    if (!kIsWeb) {
      await _mqttService.connect();
      _mqttService.dataStream.listen(_updateSensorState);
      _mqttService.relayStream.listen(_updateRelayState);
    }
  }

  void _updateSensorState(Map<String, dynamic> data) {
    setState(() {
      if (data.containsKey('temperature'))
        _temperature = (data['temperature'] as num).toDouble();
      if (data.containsKey('humidity'))
        _humidity = (data['humidity'] as num).toDouble();
      if (data.containsKey('occupancy'))
        _occupancy = (data['occupancy'] as num).toInt();
      if (data.containsKey('voltage'))
        _voltage = (data['voltage'] as num).toDouble();
      if (data.containsKey('current'))
        _current = (data['current'] as num).toDouble();
      if (data.containsKey('battery_level')) {
        _batteryLevel = (data['battery_level'] as num).toDouble();
        // Maintain last 3 values
        _batteryHistory.add(_batteryLevel);
        if (_batteryHistory.length > 3) _batteryHistory.removeAt(0);
      }
      if (data.containsKey('luminous_intensity'))
        _luminousIntensity = (data['luminous_intensity'] as num).toDouble();
      if (data.containsKey('predicted_energy_range'))
        _predictedEnergy = (data['predicted_energy_range'] as num).toDouble();
      if (data.containsKey('peak_demand'))
        _peakDemand = (data['peak_demand'] as num).toDouble();
    });
  }

  void _updatePredictionState(Map<String, dynamic> data) {
    setState(() {
      if (data.containsKey('predicted_energy_range'))
        _predictedEnergy = (data['predicted_energy_range'] as num).toDouble();
      if (data.containsKey('peak_demand'))
        _peakDemand = (data['peak_demand'] as num).toDouble();
    });
  }

  void _updateRelayState(Map<String, dynamic> data) {
    setState(() {
      if (data.containsKey('mode')) _currentMode = data['mode'];
      // Handle other relay fields if needed
    });
  }

  void _updateMode(String mode) async {
    setState(() {
      _currentMode = mode;
    });
    // Sync with backend
    await _apiService.updateSystemMode(mode);
  }

  void _toggleAI(bool enabled) {
    setState(() {
      _aiEnabled = enabled;
      if (_aiEnabled) {
        _currentMode = 'A';
        _apiService.updateSystemMode('A');
      }
    });
  }

  void _onRelayChanged(int relayId, bool state) async {
    final success = await _apiService.updateRelay(relayId, state);
    if (success) {
      // Logic for optimistic UI or wait for MQTT update
    }
  }

  @override
  Widget build(BuildContext context) {
    final List<Widget> _pages = [
      OverviewPage(
        currentMode: _currentMode,
        temperature: _temperature,
        humidity: _humidity,
        occupancy: _occupancy,
        batteryLevel: _batteryLevel,
        batteryHistory: _batteryHistory,
        luminousIntensity: _luminousIntensity,
        predictedEnergy: _predictedEnergy,
        peakDemand: _peakDemand,
        currentPower: (_current * _voltage / 1000),
      ),
      AnalyticsPage(historyData: _historyData),
      ControlsPage(
        currentMode: _currentMode,
        aiEnabled: _aiEnabled,
        onModeChanged: _updateMode,
        onAIToggled: _toggleAI,
        onRelayChanged: _onRelayChanged,
      ),
      RawDataPage(
        temperature: _temperature,
        humidity: _humidity,
        occupancy: _occupancy,
        voltage: _voltage,
        current: _current,
        batteryLevel: _batteryLevel,
        predictedEnergy: _predictedEnergy,
        peakDemand: _peakDemand,
        historyData: _historyData,
      ),
    ];

    return LayoutBuilder(
      builder: (context, constraints) {
        bool isWeb = constraints.maxWidth >= 850;

        return Scaffold(
          appBar: isWeb
              ? null
              : AppBar(
                  title: Row(
                    children: [
                      Icon(Icons.eco, color: Theme.of(context).primaryColor),
                      const SizedBox(width: 8),
                      const Text(
                        'RT-DEMS',
                        style: TextStyle(fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                  actions: [
                    IconButton(
                      icon: Icon(
                        widget.isDarkMode ? Icons.light_mode : Icons.dark_mode,
                        color: Theme.of(context).primaryColor,
                      ),
                      onPressed: widget.onThemeToggled,
                    ),
                    IconButton(
                      icon: Icon(
                        Icons.analytics_outlined,
                        color: Theme.of(context).textTheme.bodySmall?.color,
                      ),
                      onPressed: () => setState(() => _selectedIndex = 3),
                    ),
                    const SizedBox(width: 10),
                  ],
                ),
          body: isWeb
              ? Row(
                  children: [
                    _buildWebSidebar(),
                    Expanded(child: _pages[_selectedIndex]),
                  ],
                )
              : _pages[_selectedIndex],
          bottomNavigationBar: isWeb
              ? null
              : BottomNavigationBar(
                  type: BottomNavigationBarType.fixed,
                  backgroundColor: Theme.of(context).cardColor,
                  currentIndex: _selectedIndex,
                  selectedItemColor: Theme.of(context).primaryColor,
                  unselectedItemColor: Theme.of(
                    context,
                  ).textTheme.bodySmall?.color?.withAlpha(150),
                  elevation: 8,
                  onTap: (int index) {
                    setState(() {
                      _selectedIndex = index;
                    });
                  },
                  items: const [
                    BottomNavigationBarItem(
                      icon: Icon(Icons.home_outlined),
                      activeIcon: Icon(Icons.home),
                      label: 'Overview',
                    ),
                    BottomNavigationBarItem(
                      icon: Icon(Icons.bar_chart_outlined),
                      activeIcon: Icon(Icons.bar_chart),
                      label: 'Analytics',
                    ),
                    BottomNavigationBarItem(
                      icon: Icon(Icons.toggle_off_outlined),
                      activeIcon: Icon(Icons.toggle_on),
                      label: 'Controls',
                    ),
                    BottomNavigationBarItem(
                      icon: Icon(Icons.dataset_outlined),
                      activeIcon: Icon(Icons.dataset),
                      label: 'Raw Data',
                    ),
                  ],
                ),
        );
      },
    );
  }

  Widget _buildWebSidebar() {
    return Container(
      width: 250,
      color: Theme.of(context).cardColor,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.all(24.0),
            child: Row(
              children: [
                Icon(
                  Icons.eco,
                  color: Theme.of(context).primaryColor,
                  size: 32,
                ),
                const SizedBox(width: 12),
                Text(
                  'RT-DEMS',
                  style: TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                    color: Theme.of(context).textTheme.titleLarge?.color,
                  ),
                ),
              ],
            ),
          ),
          _sidebarItem(0, Icons.home_outlined, Icons.home, 'Overview'),
          _sidebarItem(
            1,
            Icons.bar_chart_outlined,
            Icons.bar_chart,
            'Analytics',
          ),
          _sidebarItem(
            2,
            Icons.toggle_off_outlined,
            Icons.toggle_on,
            'Controls',
          ),
          _sidebarItem(3, Icons.dataset_outlined, Icons.dataset, 'Raw Data'),
          const Spacer(),
          const Divider(),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: ListTile(
              leading: Icon(
                widget.isDarkMode ? Icons.light_mode : Icons.dark_mode,
                color: Theme.of(context).primaryColor,
              ),
              title: Text(
                widget.isDarkMode ? 'Light Mode' : 'Dark Mode',
                style: const TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                ),
              ),
              onTap: widget.onThemeToggled,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(10),
              ),
            ),
          ),
          ListTile(
            leading: CircleAvatar(
              backgroundColor: Theme.of(context).primaryColor.withAlpha(50),
              child: Icon(Icons.person, color: Theme.of(context).primaryColor),
            ),
            title: const Text(
              'Admin User',
              style: TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
            ),
            subtitle: const Text(
              'System Active',
              style: TextStyle(fontSize: 12),
            ),
            contentPadding: const EdgeInsets.symmetric(
              horizontal: 16,
              vertical: 8,
            ),
          ),
        ],
      ),
    );
  }

  Widget _sidebarItem(
    int index,
    IconData outlineIcon,
    IconData solidIcon,
    String label,
  ) {
    bool isSelected = _selectedIndex == index;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 4.0),
      child: InkWell(
        onTap: () => setState(() => _selectedIndex = index),
        borderRadius: BorderRadius.circular(8),
        child: Container(
          decoration: BoxDecoration(
            color: isSelected
                ? Theme.of(context).primaryColor.withAlpha(40)
                : Colors.transparent,
            borderRadius: BorderRadius.circular(8),
          ),
          padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 12.0),
          child: Row(
            children: [
              Icon(
                isSelected ? solidIcon : outlineIcon,
                color: isSelected
                    ? Theme.of(context).primaryColor
                    : Theme.of(context).textTheme.bodySmall?.color,
                size: 22,
              ),
              const SizedBox(width: 16),
              Text(
                label,
                style: TextStyle(
                  color: isSelected
                      ? Theme.of(context).primaryColor
                      : Theme.of(context).textTheme.bodyMedium?.color,
                  fontWeight: isSelected ? FontWeight.bold : FontWeight.w500,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ==========================================
// PAGE 1: OVERVIEW (Matching Image 1 & 2)
// ==========================================
class OverviewPage extends StatelessWidget {
  final String currentMode;
  final double temperature;
  final double humidity;
  final int occupancy;
  final double batteryLevel;
  final List<double> batteryHistory;
  final double luminousIntensity;
  final double predictedEnergy;
  final double peakDemand;
  final double currentPower;

  const OverviewPage({
    super.key,
    required this.currentMode,
    required this.temperature,
    required this.humidity,
    required this.occupancy,
    required this.batteryLevel,
    required this.batteryHistory,
    required this.luminousIntensity,
    required this.predictedEnergy,
    required this.peakDemand,
    required this.currentPower,
  });

  bool isBatteryStable() {
    if (batteryHistory.length < 3) return true;
    double first = batteryHistory[0];
    double last = batteryHistory.last;
    // Stable if non-decreasing or within 0.5% fluctuation
    return last >= first || (first - last).abs() < 0.5;
  }

  String getModeReasoning() {
    bool fallsBetween =
        currentPower >= predictedEnergy && currentPower <= peakDemand;
    String energyReason = fallsBetween ? "falls" : "does not fall";
    String stability = isBatteryStable() ? "stable" : "unstable";
    return "The real time energy usage $energyReason between the mean and upper bound of predicted energy usage and battery is $stability, Mode $currentMode is activated.";
  }

  @override
  Widget build(BuildContext context) {
    bool isDesktop = MediaQuery.of(context).size.width >= 850;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(24.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (isDesktop) ...[
            const Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  'Dashboard',
                  style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
                ),
                Row(
                  children: [
                    Icon(Icons.help_outline, color: Colors.grey),
                    SizedBox(width: 16),
                    Icon(Icons.dataset_outlined, color: Colors.grey),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 24),
          ],

          // Top Status Cards
          LayoutBuilder(
            builder: (context, constraints) {
              double width = constraints.maxWidth;

              if (width > 900) {
                return SizedBox(
                  height: 140, // Increased height
                  child: Row(
                    children: [
                      Expanded(
                        flex: 2,
                        child: _MetricCard(
                          title: 'System Mode',
                          value: 'Class $currentMode',
                          subtitle: getModeReasoning(),
                          icon: Icons.bolt,
                          color: currentMode == 'A'
                              ? Colors.green
                              : (currentMode == 'B'
                                    ? Colors.orange
                                    : Colors.red),
                          linePlacement: Alignment.bottomCenter,
                        ),
                      ),
                      const SizedBox(width: 24),
                      Expanded(
                        flex: 1,
                        child: _MetricCard(
                          title: 'Real-time Energy',
                          value: '${currentPower.toStringAsFixed(2)} kW',
                          icon: Icons.flash_on,
                          color: Colors.blue,
                          linePlacement: Alignment.bottomCenter,
                        ),
                      ),
                    ],
                  ),
                );
              } else {
                return GridView.count(
                  crossAxisCount: width > 600 ? 2 : 1,
                  crossAxisSpacing: 16,
                  mainAxisSpacing: 16,
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  childAspectRatio: width > 600 ? 2.2 : 1.8,
                  children: [
                    _MetricCard(
                      title: 'System Mode',
                      value: 'Class $currentMode',
                      subtitle: getModeReasoning(),
                      icon: Icons.bolt,
                      color: currentMode == 'A'
                          ? Colors.green
                          : (currentMode == 'B' ? Colors.orange : Colors.red),
                      linePlacement: Alignment.bottomCenter,
                    ),
                    _MetricCard(
                      title: 'Real-time Energy',
                      value: '${currentPower.toStringAsFixed(2)} kW',
                      icon: Icons.flash_on,
                      color: Colors.blue,
                      linePlacement: Alignment.bottomCenter,
                    ),
                  ],
                );
              }
            },
          ),
          const SizedBox(height: 24),

          // Main Layout Area
          if (isDesktop)
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  flex: 2,
                  child: _EnergyFlowGraphic(
                    currentMode: currentMode,
                    batteryLevel: batteryLevel,
                  ),
                ),
                const SizedBox(width: 24),
                Expanded(
                  flex: 1,
                  child: Column(
                    children: [
                      _PredictionDetailsCard(
                        mean: predictedEnergy,
                        upperBound: peakDemand,
                      ),
                      const SizedBox(height: 24),
                      _BatteryLagCard(
                        history: batteryHistory,
                        isStable: isBatteryStable(),
                      ),
                      const SizedBox(height: 24),
                      _EnvironmentCard(
                        temperature: temperature,
                        humidity: humidity,
                        occupancy: occupancy,
                        luminousIntensity: luminousIntensity,
                      ),
                    ],
                  ),
                ),
              ],
            )
          else
            Column(
              children: [
                _EnergyFlowGraphic(
                  currentMode: currentMode,
                  batteryLevel: batteryLevel,
                ),
                const SizedBox(height: 24),
                _PredictionDetailsCard(
                  mean: predictedEnergy,
                  upperBound: peakDemand,
                ),
                const SizedBox(height: 24),
                _BatteryLagCard(
                  history: batteryHistory,
                  isStable: isBatteryStable(),
                ),
                const SizedBox(height: 24),
                _EnvironmentCard(
                  temperature: temperature,
                  humidity: humidity,
                  occupancy: occupancy,
                  luminousIntensity: luminousIntensity,
                ),
              ],
            ),
        ],
      ),
    );
  }
}

// Custom Metric Card matching the design
class _MetricCard extends StatelessWidget {
  final String title;
  final String value;
  final String? subtitle;
  final IconData icon;
  final Color color;
  final Alignment linePlacement;

  const _MetricCard({
    required this.title,
    required this.value,
    this.subtitle,
    required this.icon,
    required this.color,
    required this.linePlacement,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        bool isSmall = constraints.maxWidth < 180;
        bool isDark = Theme.of(context).brightness == Brightness.dark;

        return Container(
          decoration: BoxDecoration(
            color: Theme.of(context).cardColor,
            borderRadius: BorderRadius.circular(12),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withOpacity(isDark ? 0.2 : 0.04),
                blurRadius: 10,
                offset: const Offset(0, 4),
              ),
            ],
          ),
          child: Stack(
            children: [
              Padding(
                padding: EdgeInsets.all(isSmall ? 10.0 : 16.0),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(icon, color: color, size: isSmall ? 32 : 42),
                        SizedBox(width: isSmall ? 8 : 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                title,
                                style: TextStyle(
                                  color: Theme.of(
                                    context,
                                  ).textTheme.bodySmall?.color,
                                  fontSize: isSmall ? 11 : 13,
                                  fontWeight: FontWeight.w500,
                                ),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                              const SizedBox(height: 4),
                              Text(
                                value,
                                style: TextStyle(
                                  fontSize: isSmall ? 18 : 24,
                                  fontWeight: FontWeight.bold,
                                  color: Theme.of(
                                    context,
                                  ).textTheme.titleLarge?.color,
                                ),
                              ),
                              if (subtitle != null) ...[
                                Text(
                                  subtitle!,
                                  style: TextStyle(
                                    color: Theme.of(context)
                                        .textTheme
                                        .bodySmall
                                        ?.color
                                        ?.withAlpha(200),
                                    fontSize: isSmall ? 9 : 11,
                                  ),
                                  maxLines: 3,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ],
                            ],
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              // Colored indicator line at bottom
              Positioned(
                bottom: 0,
                left: isSmall ? 8 : 16,
                right: isSmall ? 8 : 16,
                child: Container(
                  height: 8,
                  decoration: BoxDecoration(
                    color: color,
                    borderRadius: const BorderRadius.only(
                      topLeft: Radius.circular(4),
                      topRight: Radius.circular(4),
                    ),
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

// Energy Flow Graphic matching the dark panel in the image
class _EnergyFlowGraphic extends StatelessWidget {
  final String currentMode;
  final double batteryLevel;
  const _EnergyFlowGraphic({
    required this.currentMode,
    required this.batteryLevel,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        bool isMobile = constraints.maxWidth < 600;
        double sideWidth = isMobile ? 80 : 100;
        double hubPadding = isMobile ? 12 : 24;

        return Container(
          height: 450,
          width: double.infinity,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(16),
            gradient: const LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [Color(0xFF23364B), Color(0xFF14202E)],
            ),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withAlpha(51),
                blurRadius: 15,
                offset: const Offset(0, 8),
              ),
            ],
          ),
          child: Stack(
            alignment: Alignment.center,
            children: [
              // Background graphic aesthetic
              Positioned(
                left: -50,
                bottom: -20,
                child: Opacity(
                  opacity: 0.1,
                  child: Icon(
                    Icons.settings_input_component,
                    size: 350,
                    color: Colors.blueAccent.withAlpha(50),
                  ),
                ),
              ),

              // Nodes and lines
              Padding(
                padding: EdgeInsets.symmetric(
                  horizontal: isMobile ? 12.0 : 24.0,
                  vertical: 40.0,
                ),
                child: Row(
                  children: [
                    // 1. LEFT SIDE: Storage Node
                    SizedBox(
                      width: sideWidth * 1.2,
                      child: _GraphicNode(
                        icon: Icons.battery_charging_full,
                        label: 'Storage',
                        subtitle: '${batteryLevel.toStringAsFixed(1)}% SoC',
                        color: Colors.greenAccent,
                        isLarge: true,
                        batteryLevel: batteryLevel,
                      ),
                    ),

                    // 2. LEFT CONNECTORS: Storage -> Inverter -> Hub
                    Expanded(
                      flex: 2,
                      child: Stack(
                        alignment: Alignment.center,
                        children: [
                          // Path from Storage to Inverter
                          Row(
                            children: [
                              const Expanded(
                                child: _FlowBranch(
                                  color: Colors.greenAccent,
                                  highlight: true,
                                ),
                              ),
                              Container(
                                padding: const EdgeInsets.all(8),
                                decoration: BoxDecoration(
                                  color: Colors.blueAccent.withAlpha(40),
                                  shape: BoxShape.circle,
                                  border: Border.all(
                                    color: Colors.blueAccent,
                                    width: 2,
                                  ),
                                ),
                                child: const Icon(
                                  Icons.settings_input_component,
                                  color: Colors.blueAccent,
                                  size: 20,
                                ),
                              ),
                              const Expanded(
                                child: _FlowBranch(
                                  color: Colors.blueAccent,
                                  highlight: true,
                                  bendUp: true,
                                ),
                              ),
                            ],
                          ),
                          Positioned(
                            bottom: 0,
                            left: 0,
                            right: 0,
                            child: Center(
                              child: Text(
                                'Inverter',
                                style: TextStyle(
                                  color: Colors.white.withAlpha(150),
                                  fontSize: 10,
                                  fontWeight: FontWeight.w500,
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),

                    // 3. CENTER: RT-DEMS HUB
                    Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Container(
                          padding: EdgeInsets.symmetric(
                            horizontal: hubPadding,
                            vertical: 30,
                          ),
                          decoration: BoxDecoration(
                            color: Colors.white.withAlpha(15),
                            borderRadius: BorderRadius.circular(24),
                            border: Border.all(
                              color: Colors.tealAccent,
                              width: 1.5,
                            ),
                            boxShadow: [
                              BoxShadow(
                                color: Colors.tealAccent.withAlpha(50),
                                blurRadius: 30,
                                spreadRadius: -5,
                              ),
                            ],
                          ),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                Icons.hub_outlined,
                                color: Colors.tealAccent,
                                size: isMobile ? 40 : 54,
                              ),
                              const SizedBox(height: 12),
                              Text(
                                'RT-DEMS',
                                style: TextStyle(
                                  color: Colors.white,
                                  fontSize: isMobile ? 14 : 18,
                                  fontWeight: FontWeight.bold,
                                  letterSpacing: 1.5,
                                ),
                              ),
                              Text(
                                'CENTRAL HUB',
                                style: TextStyle(
                                  color: Colors.tealAccent.withAlpha(180),
                                  fontSize: 8,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),

                    // 4. RIGHT CONNECTORS: Hub -> Tiers
                    Expanded(
                      flex: 2,
                      child: SizedBox(
                        height: 180,
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                          children: [
                            _FlowBranch(
                              color: Colors.greenAccent,
                              highlight: currentMode == 'A',
                              bendUp: true,
                            ),
                            _FlowBranch(
                              color: Colors.orangeAccent,
                              highlight:
                                  currentMode == 'A' || currentMode == 'B',
                            ),
                            _FlowBranch(
                              color: Colors.redAccent,
                              highlight: true,
                              bendDown: true,
                            ),
                          ],
                        ),
                      ),
                    ),

                    // 5. RIGHT SIDE: Appliance Tiers
                    SizedBox(
                      width: sideWidth * 1.2,
                      child: SizedBox(
                        height: 220,
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                          children: [
                            _GraphicNode(
                              icon: Icons.check_circle_outline,
                              label: 'Tier A',
                              subtitle: 'Essential',
                              color: Colors.greenAccent,
                              small: true,
                              highlight: currentMode == 'A',
                            ),
                            _GraphicNode(
                              icon: Icons.wb_incandescent_outlined,
                              label: 'Tier B',
                              subtitle: 'Prioritized',
                              color: Colors.orangeAccent,
                              small: true,
                              highlight:
                                  currentMode == 'A' || currentMode == 'B',
                            ),
                            _GraphicNode(
                              icon: Icons.block,
                              label: 'Tier C',
                              subtitle: 'Load Shed',
                              color: Colors.redAccent,
                              small: true,
                              highlight: true,
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

// Sub-widgets for the graphic

class _FlowBranch extends StatelessWidget {
  final Color color;
  final bool reverse;
  final bool highlight;
  final bool bendUp;
  final bool bendDown;

  const _FlowBranch({
    required this.color,
    this.reverse = false,
    this.highlight = false,
    this.bendUp = false,
    this.bendDown = false,
  });

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: highlight ? 1.0 : 0.2,
      child: CustomPaint(
        size: const Size(double.infinity, 40),
        painter: _BentFlowPainter(
          color: color,
          reverse: reverse,
          highlight: highlight,
          bendUp: bendUp,
          bendDown: bendDown,
        ),
      ),
    );
  }
}

class _BentFlowPainter extends CustomPainter {
  final Color color;
  final bool reverse;
  final bool highlight;
  final bool bendUp;
  final bool bendDown;

  _BentFlowPainter({
    required this.color,
    required this.reverse,
    required this.highlight,
    required this.bendUp,
    required this.bendDown,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5
      ..strokeCap = StrokeCap.round;

    final path = Path();
    double midX = size.width / 2;
    double cornerSize = 12.0;

    if (reverse) {
      // Flow from RIGHT to LEFT
      path.moveTo(size.width, size.height / 2);
      if (bendUp) {
        path.lineTo(midX + cornerSize, size.height / 2);
        path.quadraticBezierTo(
          midX,
          size.height / 2,
          midX,
          size.height / 2 - cornerSize,
        );
        path.lineTo(midX, cornerSize);
        path.quadraticBezierTo(midX, 0, midX - cornerSize, 0);
        path.lineTo(0, 0);
      } else if (bendDown) {
        path.lineTo(midX + cornerSize, size.height / 2);
        path.quadraticBezierTo(
          midX,
          size.height / 2,
          midX,
          size.height / 2 + cornerSize,
        );
        path.lineTo(midX, size.height - cornerSize);
        path.quadraticBezierTo(
          midX,
          size.height,
          midX - cornerSize,
          size.height,
        );
        path.lineTo(0, size.height);
      } else {
        path.lineTo(0, size.height / 2);
      }
    } else {
      // Flow from LEFT to RIGHT
      path.moveTo(0, size.height / 2);
      if (bendUp) {
        path.lineTo(midX - cornerSize, size.height / 2);
        path.quadraticBezierTo(
          midX,
          size.height / 2,
          midX,
          size.height / 2 - cornerSize,
        );
        path.lineTo(midX, cornerSize);
        path.quadraticBezierTo(midX, 0, midX + cornerSize, 0);
        path.lineTo(size.width, 0);
      } else if (bendDown) {
        path.lineTo(midX - cornerSize, size.height / 2);
        path.quadraticBezierTo(
          midX,
          size.height / 2,
          midX,
          size.height / 2 + cornerSize,
        );
        path.lineTo(midX, size.height - cornerSize);
        path.quadraticBezierTo(
          midX,
          size.height,
          midX + cornerSize,
          size.height,
        );
        path.lineTo(size.width, size.height);
      } else {
        path.lineTo(size.width, size.height / 2);
      }
    }

    canvas.drawPath(path, paint);

    // Draw arrowhead at the end of the path
    double arrowSize = 6.0;
    double endX = reverse ? 0 : size.width;
    double endY = bendUp ? 0 : (bendDown ? size.height : size.height / 2);

    Path arrowPath = Path();
    if (reverse) {
      arrowPath.moveTo(endX + arrowSize, endY - arrowSize);
      arrowPath.lineTo(endX, endY);
      arrowPath.lineTo(endX + arrowSize, endY + arrowSize);
    } else {
      arrowPath.moveTo(endX - arrowSize, endY - arrowSize);
      arrowPath.lineTo(endX, endY);
      arrowPath.lineTo(endX - arrowSize, endY + arrowSize);
    }
    canvas.drawPath(arrowPath, paint);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => true;
}

class _GraphicNode extends StatelessWidget {
  final IconData icon;
  final String label;
  final String? subtitle;
  final Color color;
  final bool small;
  final bool highlight;
  final bool isLarge;
  final double? batteryLevel;

  const _GraphicNode({
    required this.icon,
    required this.label,
    this.subtitle,
    required this.color,
    this.small = false,
    this.highlight = true,
    this.isLarge = false,
    this.batteryLevel,
  });

  @override
  Widget build(BuildContext context) {
    const Color fixedCardColor = Color(0xFF1E2E3D);
    const Color fixedTextColor = Colors.white;
    const Color fixedSubtitleColor = Colors.white70;

    if (isLarge) {
      double level = batteryLevel ?? 0.0;
      return Container(
        padding: const EdgeInsets.symmetric(vertical: 24, horizontal: 16),
        decoration: BoxDecoration(
          color: fixedCardColor,
          borderRadius: BorderRadius.circular(16),
          boxShadow: [
            BoxShadow(
              color: color.withAlpha(30),
              blurRadius: 15,
              spreadRadius: 2,
            ),
          ],
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Energy Storage',
              style: TextStyle(
                color: fixedSubtitleColor,
                fontSize: 10,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 12),
            Container(
              height: 100,
              width: 50,
              decoration: BoxDecoration(
                color: color.withAlpha(20),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: color.withAlpha(50), width: 1),
              ),
              child: Stack(
                alignment: Alignment.bottomCenter,
                children: [
                  Container(
                    height: level, // level% SoC
                    decoration: BoxDecoration(
                      color: color,
                      borderRadius: BorderRadius.circular(level > 2 ? 7 : 2),
                    ),
                  ),
                  const Center(
                    child: Icon(Icons.flash_on, color: Colors.white, size: 20),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            Text(
              '${level.toStringAsFixed(1)}%',
              style: const TextStyle(
                color: fixedTextColor,
                fontSize: 22,
                fontWeight: FontWeight.bold,
              ),
            ),
            const Text(
              'SoC',
              style: TextStyle(color: fixedSubtitleColor, fontSize: 10),
            ),
            const SizedBox(height: 8),
            Text(
              label,
              style: TextStyle(
                color: color,
                fontSize: 12,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
      );
    }

    return Opacity(
      opacity: highlight ? 1.0 : 0.4,
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: fixedCardColor,
          borderRadius: BorderRadius.circular(15),
          boxShadow: highlight
              ? [
                  BoxShadow(
                    color: color.withAlpha(50),
                    blurRadius: 10,
                    spreadRadius: 1,
                  ),
                ]
              : null,
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: color.withAlpha(30),
                shape: BoxShape.circle,
              ),
              child: Icon(icon, color: color, size: small ? 18 : 24),
            ),
            const SizedBox(width: 12),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  label,
                  style: TextStyle(
                    color: fixedTextColor,
                    fontSize: small ? 11 : 13,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                if (subtitle != null)
                  Text(
                    subtitle!,
                    style: TextStyle(
                      color: fixedSubtitleColor,
                      fontSize: small ? 9 : 10,
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _PredictionDetailsCard extends StatelessWidget {
  final double mean;
  final double upperBound;

  const _PredictionDetailsCard({required this.mean, required this.upperBound});

  @override
  Widget build(BuildContext context) {
    bool isDark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(isDark ? 0.2 : 0.04),
            blurRadius: 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Predicted Energy Data',
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
          ),
          const SizedBox(height: 16),
          _detailRow(
            context,
            'Mean Prediction',
            '${mean.toStringAsFixed(2)} kW',
            Colors.blue,
            Icons.auto_graph,
          ),
          const SizedBox(height: 12),
          _detailRow(
            context,
            'Upper Bound (95%)',
            '${upperBound.toStringAsFixed(2)} kW',
            Colors.orange,
            Icons.trending_up,
          ),
        ],
      ),
    );
  }

  Widget _detailRow(
    BuildContext context,
    String label,
    String val,
    Color color,
    IconData icon,
  ) {
    return Row(
      children: [
        Icon(icon, color: color, size: 18),
        const SizedBox(width: 12),
        Expanded(
          child: Text(
            label,
            style: TextStyle(
              color: Theme.of(context).textTheme.bodySmall?.color,
              fontSize: 13,
            ),
          ),
        ),
        Text(
          val,
          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
        ),
      ],
    );
  }
}

class _BatteryLagCard extends StatelessWidget {
  final List<double> history;
  final bool isStable;

  const _BatteryLagCard({required this.history, required this.isStable});

  @override
  Widget build(BuildContext context) {
    bool isDark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(isDark ? 0.2 : 0.04),
            blurRadius: 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text(
                '3-Time Battery Lag',
                style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: (isStable ? Colors.green : Colors.red).withAlpha(50),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  isStable ? 'STABLE' : 'UNSTABLE',
                  style: TextStyle(
                    color: isStable ? Colors.green : Colors.red,
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: history.map((val) => _lagItem(context, val)).toList(),
          ),
        ],
      ),
    );
  }

  Widget _lagItem(BuildContext context, double val) {
    return Column(
      children: [
        Text(
          '${val.toStringAsFixed(1)}%',
          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
        ),
        Text(
          't-${history.indexOf(val)}',
          style: TextStyle(
            color: Theme.of(context).textTheme.bodySmall?.color,
            fontSize: 10,
          ),
        ),
      ],
    );
  }
}

class _EnvironmentCard extends StatelessWidget {
  final double temperature;
  final double humidity;
  final int occupancy;
  final double luminousIntensity;

  const _EnvironmentCard({
    required this.temperature,
    required this.humidity,
    required this.occupancy,
    required this.luminousIntensity,
  });

  @override
  Widget build(BuildContext context) {
    bool isDark = Theme.of(context).brightness == Brightness.dark;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(isDark ? 0.2 : 0.04),
            blurRadius: 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Environment Context',
            style: TextStyle(
              fontWeight: FontWeight.bold,
              fontSize: 16,
              color: Theme.of(context).textTheme.titleLarge?.color,
            ),
          ),
          const SizedBox(height: 20),
          Row(
            children: [
              Expanded(
                child: _envItem(
                  context,
                  occupancy == 1 ? Icons.people : Icons.people_outline,
                  'Occupancy',
                  occupancy == 1 ? 'Occupied' : 'Empty',
                  occupancy == 1 ? Colors.blue : Colors.grey,
                ),
              ),
              Expanded(
                child: _envItem(
                  context,
                  Icons.wb_sunny_outlined,
                  'Luminous Intensity',
                  '${luminousIntensity.toStringAsFixed(0)} lux',
                  Colors.amber,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: _envItem(
                  context,
                  Icons.thermostat,
                  'Temp',
                  '${temperature.toStringAsFixed(1)}°C',
                  Colors.orange,
                ),
              ),
              Expanded(
                child: _envItem(
                  context,
                  Icons.water_drop_outlined,
                  'Humidity',
                  '${humidity.toStringAsFixed(1)}%',
                  Colors.cyan,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
        ],
      ),
    );
  }

  Widget _envItem(
    BuildContext context,
    IconData icon,
    String label,
    String value,
    Color color,
  ) {
    bool isDark = Theme.of(context).brightness == Brightness.dark;
    return Row(
      children: [
        Container(
          padding: const EdgeInsets.all(8),
          decoration: BoxDecoration(
            color: color.withAlpha(isDark ? 40 : 25),
            shape: BoxShape.circle,
          ),
          child: Icon(icon, color: color, size: 18),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                label,
                style: TextStyle(
                  color: Theme.of(context).textTheme.bodySmall?.color,
                  fontSize: 11,
                ),
                overflow: TextOverflow.ellipsis,
              ),
              Text(
                value,
                style: TextStyle(
                  color: Theme.of(context).textTheme.bodyLarge?.color,
                  fontWeight: FontWeight.bold,
                  fontSize: 13,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// ==========================================
// PAGE 2: ANALYTICS (Charts matching Image 3)
// ==========================================
class AnalyticsPage extends StatelessWidget {
  final List<dynamic> historyData;
  const AnalyticsPage({super.key, required this.historyData});

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(24.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Analytics',
            style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 24),

          // Chart Container
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(12),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withOpacity(0.04),
                  blurRadius: 10,
                  offset: const Offset(0, 4),
                ),
              ],
            ),
            child: Column(
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Text(
                      'Energy History',
                      style: TextStyle(
                        fontWeight: FontWeight.bold,
                        fontSize: 16,
                      ),
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 12,
                        vertical: 6,
                      ),
                      decoration: BoxDecoration(
                        border: Border.all(color: Colors.grey.shade300),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Row(
                        children: [
                          Text(
                            'Last 7 Days',
                            style: TextStyle(
                              color: Colors.grey.shade700,
                              fontSize: 13,
                            ),
                          ),
                          const SizedBox(width: 4),
                          Icon(
                            Icons.keyboard_arrow_down,
                            color: Colors.grey.shade700,
                            size: 16,
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 30),
                SizedBox(
                  height: 300,
                  child: BarChart(
                    BarChartData(
                      alignment: BarChartAlignment.spaceAround,
                      maxY: 160,
                      barTouchData: BarTouchData(enabled: false),
                      titlesData: FlTitlesData(
                        show: true,
                        bottomTitles: AxisTitles(
                          sideTitles: SideTitles(
                            showTitles: true,
                            getTitlesWidget: (value, meta) {
                              return Padding(
                                padding: const EdgeInsets.only(top: 8.0),
                                child: Text(
                                  value.toInt().toString(),
                                  style: const TextStyle(
                                    color: Colors.grey,
                                    fontSize: 12,
                                  ),
                                ),
                              );
                            },
                          ),
                        ),
                        leftTitles: AxisTitles(
                          sideTitles: SideTitles(
                            showTitles: true,
                            reservedSize: 40,
                            getTitlesWidget: (value, meta) {
                              if (value % 30 == 0) {
                                return Text(
                                  value.toInt().toString(),
                                  style: const TextStyle(
                                    color: Colors.grey,
                                    fontSize: 12,
                                  ),
                                );
                              }
                              return const SizedBox.shrink();
                            },
                          ),
                        ),
                        rightTitles: const AxisTitles(
                          sideTitles: SideTitles(showTitles: false),
                        ),
                        topTitles: const AxisTitles(
                          sideTitles: SideTitles(showTitles: false),
                        ),
                      ),
                      gridData: FlGridData(
                        show: true,
                        drawVerticalLine: false,
                        horizontalInterval: 30,
                        getDrawingHorizontalLine: (value) =>
                            FlLine(color: Colors.grey.shade200, strokeWidth: 1),
                      ),
                      borderData: FlBorderData(show: false),
                      barGroups: historyData.isEmpty
                          ? [
                              _makeGroupData(1, 80, 70),
                              _makeGroupData(2, 60, 65),
                              _makeGroupData(3, 110, 80),
                              _makeGroupData(4, 150, 70),
                              _makeGroupData(5, 70, 90),
                              _makeGroupData(6, 125, 120),
                              _makeGroupData(7, 85, 80),
                            ]
                          : List.generate(
                              historyData.length > 7 ? 7 : historyData.length,
                              (index) {
                                final item = historyData[index];
                                double current = (item['current'] ?? 0.0)
                                    .toDouble();
                                double voltage = (item['voltage'] ?? 220.0)
                                    .toDouble();
                                double consumption = (current * voltage / 1000);
                                // Simulation for solar if not present
                                double solar =
                                    consumption * (0.5 + (index % 5) / 10);
                                return _makeGroupData(
                                  index + 1,
                                  solar,
                                  consumption,
                                );
                              },
                            ),
                    ),
                  ),
                ),
                const SizedBox(height: 20),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    _legendItem(
                      context,
                      Colors.green.shade400,
                      'Solar Generation',
                    ),
                    const SizedBox(width: 24),
                    _legendItem(
                      context,
                      Colors.blue.shade400,
                      'Home Consumption',
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),

          // Summary Cards Row
          LayoutBuilder(
            builder: (context, constraints) {
              bool isMobile = constraints.maxWidth < 600;
              return Flex(
                direction: isMobile ? Axis.vertical : Axis.horizontal,
                children: [
                  Expanded(
                    flex: isMobile ? 0 : 1,
                    child: _SummaryCard(
                      title: 'Total Generated',
                      value: '210 kWh',
                    ),
                  ),
                  if (!isMobile)
                    const SizedBox(width: 16)
                  else
                    const SizedBox(height: 16),
                  Expanded(
                    flex: isMobile ? 0 : 1,
                    child: _SummaryCard(
                      title: 'Total Consumed',
                      value: '150 kWh',
                    ),
                  ),
                  if (!isMobile)
                    const SizedBox(width: 16)
                  else
                    const SizedBox(height: 16),
                  Expanded(
                    flex: isMobile ? 0 : 1,
                    child: _SummaryCard(
                      title: 'Net Grid Independence',
                      value: '85%',
                    ),
                  ),
                ],
              );
            },
          ),
        ],
      ),
    );
  }

  BarChartGroupData _makeGroupData(int x, double y1, double y2) {
    return BarChartGroupData(
      barsSpace: 4,
      x: x,
      barRods: [
        BarChartRodData(
          toY: y1,
          color: Colors.green.shade400,
          width: 14,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(4)),
        ),
        BarChartRodData(
          toY: y2,
          color: Colors.blue.shade400,
          width: 14,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(4)),
        ),
      ],
    );
  }

  Widget _legendItem(BuildContext context, Color color, String text) {
    return Row(
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
        const SizedBox(width: 8),
        Text(
          text,
          style: TextStyle(
            color: Theme.of(context).textTheme.bodySmall?.color,
            fontSize: 13,
            fontWeight: FontWeight.w500,
          ),
        ),
      ],
    );
  }
}

class _SummaryCard extends StatelessWidget {
  final String title;
  final String value;

  const _SummaryCard({required this.title, required this.value});

  @override
  Widget build(BuildContext context) {
    bool isDark = Theme.of(context).brightness == Brightness.dark;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(isDark ? 0.2 : 0.04),
            blurRadius: 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: TextStyle(
              color: Theme.of(context).textTheme.bodySmall?.color,
              fontSize: 14,
              fontWeight: FontWeight.w500,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            value,
            style: TextStyle(
              fontSize: 24,
              fontWeight: FontWeight.bold,
              color: Theme.of(context).textTheme.titleLarge?.color,
            ),
          ),
        ],
      ),
    );
  }
}

// ==========================================
// PAGE 3: CONTROLS (Relay Toggles)
// ==========================================
class ControlsPage extends StatefulWidget {
  final String currentMode;
  final bool aiEnabled;
  final Function(String) onModeChanged;
  final Function(bool) onAIToggled;
  final Function(int, bool) onRelayChanged;

  const ControlsPage({
    super.key,
    required this.currentMode,
    required this.aiEnabled,
    required this.onModeChanged,
    required this.onAIToggled,
    required this.onRelayChanged,
  });

  @override
  State<ControlsPage> createState() => _ControlsPageState();
}

class _ControlsPageState extends State<ControlsPage> {
  // Manual relay states (local for now, but influenced by mode)
  bool r1WaterHeater = true;
  bool r2AC = true;
  bool r3Fridge = true;
  bool r4Lights = true;

  void _syncRelaysWithMode(String mode) {
    setState(() {
      if (mode == 'A') {
        r1WaterHeater = true;
        r2AC = true;
        r3Fridge = true;
        r4Lights = true;
      } else if (mode == 'B') {
        r1WaterHeater = false;
        r2AC = false;
        r3Fridge = true;
        r4Lights = true;
      } else if (mode == 'C') {
        r1WaterHeater = false;
        r2AC = false;
        r3Fridge = false;
        r4Lights = true;
      }
    });
  }

  @override
  void initState() {
    super.initState();
    _syncRelaysWithMode(widget.currentMode);
  }

  @override
  void didUpdateWidget(ControlsPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.currentMode != widget.currentMode) {
      _syncRelaysWithMode(widget.currentMode);
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(24.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Appliance Controls',
            style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 8),
          Text(
            "Manage hardware relays. AI automatically switches modes based on stability and demand.",
            style: TextStyle(color: Colors.grey.shade600),
          ),
          const SizedBox(height: 24),

          // AI Management Switch
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              color: Theme.of(context).cardColor,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: widget.aiEnabled
                    ? Colors.green.withAlpha(100)
                    : (Theme.of(context).brightness == Brightness.dark
                          ? Colors.white12
                          : Colors.grey.shade300),
              ),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withOpacity(
                    Theme.of(context).brightness == Brightness.dark
                        ? 0.2
                        : 0.04,
                  ),
                  blurRadius: 10,
                  offset: const Offset(0, 4),
                ),
              ],
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'AI Auto-Management',
                      style: TextStyle(
                        fontWeight: FontWeight.bold,
                        fontSize: 16,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      widget.aiEnabled
                          ? 'Active: Mode ${widget.currentMode}'
                          : 'Manual Override Active',
                      style: TextStyle(
                        color: widget.aiEnabled
                            ? Colors.green.shade700
                            : Colors.orange,
                        fontSize: 13,
                      ),
                    ),
                  ],
                ),
                Switch(
                  value: widget.aiEnabled,
                  activeColor: Colors.green,
                  onChanged: (val) => widget.onAIToggled(val),
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),

          // Mode Selection (Visible/Active when AI is disabled)
          if (!widget.aiEnabled) ...[
            const Text(
              'Select System Mode',
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                _modeButton('A', 'Max Load', Colors.green),
                const SizedBox(width: 12),
                _modeButton('B', 'Prioritized', Colors.orange),
                const SizedBox(width: 12),
                _modeButton('C', 'Load Shed', Colors.red),
              ],
            ),
            const SizedBox(height: 24),
          ],

          _buildRelayCard(
            'Water Heater (Relay 1)',
            r1WaterHeater,
            Icons.hot_tub,
            (val) {
              setState(() => r1WaterHeater = val);
              widget.onRelayChanged(1, val);
            },
          ),
          _buildRelayCard('HVAC / A.C. (Relay 2)', r2AC, Icons.ac_unit, (val) {
            setState(() => r2AC = val);
            widget.onRelayChanged(2, val);
          }),
          _buildRelayCard('Freezer (Relay 3)', r3Fridge, Icons.kitchen, (val) {
            setState(() => r3Fridge = val);
            widget.onRelayChanged(3, val);
          }),
          _buildRelayCard(
            'Lighting (Relay 4)',
            r4Lights,
            Icons.lightbulb_outline,
            (val) {
              setState(() => r4Lights = val);
              widget.onRelayChanged(4, val);
            },
          ),
        ],
      ),
    );
  }

  Widget _modeButton(String mode, String label, Color color) {
    bool isSelected = widget.currentMode == mode;
    return Expanded(
      child: InkWell(
        onTap: () => widget.onModeChanged(mode),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 12),
          decoration: BoxDecoration(
            color: isSelected
                ? color.withAlpha(51)
                : Theme.of(context).cardColor,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: isSelected
                  ? color
                  : (Theme.of(context).brightness == Brightness.dark
                        ? Colors.white12
                        : Colors.grey.shade300),
              width: isSelected ? 2 : 1,
            ),
          ),
          child: Column(
            children: [
              Text(
                'Mode $mode',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: isSelected
                      ? color
                      : Theme.of(context).textTheme.bodyLarge?.color,
                ),
              ),
              Text(
                label,
                style: TextStyle(
                  fontSize: 10,
                  color: isSelected
                      ? color.withAlpha(200)
                      : Theme.of(context).textTheme.bodySmall?.color,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildRelayCard(
    String title,
    bool value,
    IconData icon,
    Function(bool) onChanged,
  ) {
    return Container(
      margin: const EdgeInsets.only(bottom: 16),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(
              Theme.of(context).brightness == Brightness.dark ? 0.2 : 0.02,
            ),
            blurRadius: 5,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
        leading: CircleAvatar(
          backgroundColor: value
              ? Colors.green.withAlpha(50)
              : (Theme.of(context).brightness == Brightness.dark
                    ? Colors.white10
                    : Colors.grey.shade100),
          child: Icon(
            icon,
            color: value
                ? (Theme.of(context).brightness == Brightness.dark
                      ? Colors.greenAccent
                      : Colors.green.shade600)
                : (Theme.of(context).brightness == Brightness.dark
                      ? Colors.white38
                      : Colors.grey.shade500),
          ),
        ),
        title: Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(
          widget.aiEnabled
              ? 'Unlocked by Mode ${widget.currentMode}'
              : 'Manual Control',
          style: TextStyle(
            fontSize: 12,
            color: Theme.of(context).textTheme.bodySmall?.color,
          ),
        ),
        trailing: Switch(
          value: value,
          activeColor: Colors.green,
          onChanged: widget.aiEnabled
              ? null
              : onChanged, // Disabled if AI is managing
        ),
      ),
    );
  }
}

// ==========================================
// PAGE 4: NOTIFICATIONS (Replaces Logs)
// ==========================================
// ==========================================
// PAGE 4: RAW DATA (Replaces Notifications)
// ==========================================
class RawDataPage extends StatelessWidget {
  final double temperature;
  final double humidity;
  final int occupancy;
  final double voltage;
  final double current;
  final double batteryLevel;
  final double predictedEnergy;
  final double peakDemand;
  final List<dynamic> historyData;

  const RawDataPage({
    super.key,
    required this.temperature,
    required this.humidity,
    required this.occupancy,
    required this.voltage,
    required this.current,
    required this.batteryLevel,
    required this.predictedEnergy,
    required this.peakDemand,
    required this.historyData,
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(24.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Raw System Data',
            style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 8),
          Text(
            "Low-level telemetry and environmental context variables.",
            style: TextStyle(
              color: Theme.of(context).textTheme.bodySmall?.color,
            ),
          ),
          const SizedBox(height: 24),

          // Live Metrics Grid
          GridView.count(
            crossAxisCount: MediaQuery.of(context).size.width > 600 ? 3 : 1,
            crossAxisSpacing: 16,
            mainAxisSpacing: 16,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            childAspectRatio: 2.5,
            children: [
              _RawMetricItem(
                label: 'Voltage',
                value: '${voltage.toStringAsFixed(1)} V',
                icon: Icons.flash_on,
                color: Colors.blue,
              ),
              _RawMetricItem(
                label: 'Current',
                value: '${current.toStringAsFixed(2)} A',
                icon: Icons.electric_meter,
                color: Colors.orange,
              ),
              _RawMetricItem(
                label: 'Battery SoC',
                value: '${batteryLevel.toStringAsFixed(1)} %',
                icon: Icons.battery_full,
                color: Colors.green,
              ),
              _RawMetricItem(
                label: 'Temperature',
                value: '${temperature.toStringAsFixed(1)} °C',
                icon: Icons.thermostat,
                color: Colors.redAccent,
              ),
              _RawMetricItem(
                label: 'Humidity',
                value: '${humidity.toStringAsFixed(1)} %',
                icon: Icons.water_drop,
                color: Colors.cyan,
              ),
              _RawMetricItem(
                label: 'Occupancy',
                value: occupancy == 1 ? 'Present' : 'Absent',
                icon: Icons.people,
                color: Colors.purple,
              ),
            ],
          ),

          const SizedBox(height: 32),
          const Text(
            'Recent Data Points',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 16),

          // History Table
          Container(
            width: double.infinity,
            decoration: BoxDecoration(
              color: Theme.of(context).cardColor,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: Theme.of(context).dividerColor.withAlpha(50),
              ),
            ),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: DataTable(
                columnSpacing: 12,
                headingRowHeight: 48,
                dataRowMaxHeight: 48,
                columns: const [
                  DataColumn(
                    label: Text(
                      'Time',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                  DataColumn(
                    label: Text(
                      'Power (W)',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                  DataColumn(
                    label: Text(
                      'Temp',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                  DataColumn(
                    label: Text(
                      'Bat %',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                ],
                rows: historyData.reversed.take(10).map((data) {
                  final timestamp = data['timestamp'] != null
                      ? DateTime.parse(data['timestamp']).toLocal()
                      : DateTime.now();
                  final power =
                      ((data['voltage'] ?? 0) * (data['current'] ?? 0))
                          .toStringAsFixed(0);

                  return DataRow(
                    cells: [
                      DataCell(
                        Text(
                          '${timestamp.hour}:${timestamp.minute.toString().padLeft(2, '0')}:${timestamp.second.toString().padLeft(2, '0')}',
                        ),
                      ),
                      DataCell(Text(power)),
                      DataCell(
                        Text(
                          '${data['temperature']?.toStringAsFixed(1) ?? "N/A"}°',
                        ),
                      ),
                      DataCell(
                        Text(
                          '${data['battery_level']?.toStringAsFixed(0) ?? "N/A"}%',
                        ),
                      ),
                    ],
                  );
                }).toList(),
              ),
            ),
          ),
          if (historyData.isEmpty)
            Padding(
              padding: const EdgeInsets.all(32.0),
              child: Center(
                child: Text(
                  "No historical data available yet.",
                  style: TextStyle(
                    color: Theme.of(context).textTheme.bodySmall?.color,
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _RawMetricItem extends StatelessWidget {
  final String label;
  final String value;
  final IconData icon;
  final Color color;

  const _RawMetricItem({
    required this.label,
    required this.value,
    required this.icon,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.04),
            blurRadius: 4,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Row(
        children: [
          Icon(icon, color: color, size: 28),
          const SizedBox(width: 16),
          Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                label,
                style: TextStyle(
                  fontSize: 12,
                  color: Theme.of(context).textTheme.bodySmall?.color,
                ),
              ),
              Text(
                value,
                style: const TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
