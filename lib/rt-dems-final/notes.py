import time

# ==========================================
# SMART ENERGY MANAGEMENT SYSTEM
# ==========================================

# LOAD THRESHOLDS
BASELINE_THRESHOLD = 30
MODERATE_THRESHOLD = 50
PEAK_THRESHOLD = 80


# ==========================================
# FUNCTION TO CHECK BATTERY STABILITY
# ==========================================

def battery_stable(levels, threshold):

    for level in levels:

        if level < threshold:
            return False

    return True


# ==========================================
# FUNCTION TO ENERGIZE RELAYS
# ==========================================

def energize_relays(relay1=False, relay2=False, relay3=False):

    print("\nRelay Status")

    print(f"Relay 1: {'ON' if relay1 else 'OFF'}")
    print(f"Relay 2: {'ON' if relay2 else 'OFF'}")
    print(f"Relay 3: {'ON' if relay3 else 'OFF'}")


# ==========================================
# MAIN PROGRAM LOOP
# ==========================================

while True:

    print("\n====================================")
    print(" SMART ENERGY MANAGEMENT SYSTEM ")
    print("====================================")

    # ======================================
    # CONTEXT-AWARE ENVIRONMENTAL MONITORING
    # ======================================

    time_of_day = input("\nEnter Time of Day: ")
    day = input("Enter Day of Week: ")

    temperature = float(input("Enter Temperature: "))
    humidity = float(input("Enter Humidity: "))
    occupancy = int(input("Enter Occupancy: "))

    # ======================================
    # ENERGY DEMAND FORECAST INTERVAL
    # ======================================

    EDFI = float(input("\nEnter EDFI Prediction Value: "))

    # ======================================
    # BATTERY VALUES FOR 3 TIME LAGS
    # ======================================

    battery_levels = []

    print("\nEnter Battery Levels for 3 Consecutive Time Lags")

    for i in range(3):

        level = float(input(f"Battery Level {i+1}: "))
        battery_levels.append(level)

    # ======================================
    # PEAK LOAD CONDITION
    # ======================================

    if EDFI >= PEAK_THRESHOLD:

        print("\nPEAK LOAD DETECTED")

        if battery_stable(battery_levels, 80):

            print("ACTION: Switch to Smart A")

            # ENERGIZE RELAY 1,2,3
            energize_relays(True, True, True)

        elif battery_stable(battery_levels, 60):

            print("ACTION: Switch to Smart B")

            # ENERGIZE RELAY 2,3
            energize_relays(False, True, True)

        else:

            print("ACTION: Switch to Smart C (Baseline Load)")

            # ENERGIZE RELAY 3
            energize_relays(False, False, True)

    # ======================================
    # MODERATE LOAD CONDITION
    # ======================================

    elif MODERATE_THRESHOLD <= EDFI < PEAK_THRESHOLD:

        print("\nMODERATE LOAD DETECTED")

        if battery_stable(battery_levels, 60):

            print("ACTION: Switch to Smart B")

            # ENERGIZE RELAY 2,3
            energize_relays(False, True, True)

        else:

            print("ACTION: Switch to Smart C (Baseline Load)")

            # ENERGIZE RELAY 3
            energize_relays(False, False, True)

    # ======================================
    # BASELINE LOAD CONDITION
    # ======================================

    elif BASELINE_THRESHOLD <= EDFI < MODERATE_THRESHOLD:

        print("\nBASELINE LOAD DETECTED")
        print("ACTION: Switch to Smart C")

        # ENERGIZE RELAY 3
        energize_relays(False, False, True)

    # ======================================
    # VERY LOW LOAD CONDITION
    # ======================================

    else:

        print("\nVERY LOW LOAD DETECTED")
        print("ACTION: Minimal Power Usage Mode")

        # TURN OFF ALL RELAYS
        energize_relays(False, False, False)

    # ======================================
    # MONITORING INTERVAL
    # ======================================
    print("\nWaiting for next monitoring cycle...")

    

    time.sleep(5)

    print("\nSystem Restarting...\n")