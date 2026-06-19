# Understanding Energy Metrics: E_wh & Group 1's "Live Energy (1 min)"

Our calculated energy `E_wh` (cumulative Watt-hours) is designed to match Group 1's **Live Energy (1 min)**.

## How it is Calculated:
1. **Sliding Window:** We maintain a sliding buffer of the last **60 power readings** (taken exactly once per second, representing the last 60 seconds).
2. **Watt-Seconds:** The sum of these 60 power readings represents the energy consumed in the last minute (in Watt-seconds).
3. **Conversion:** This sum is divided by **3600** to convert it to Watt-hours (Wh).
   $$\text{E\_wh} = \frac{\sum_{i=1}^{60} P_i}{3600}$$
4. **Model Delivery:** This calculated 1-minute sliding value is published in the `room/sensors` MQTT payload as `energy_kw` (and `energy_wh`) to be processed by the ML model.

This ensures our `E_wh` is identical in scale and duration to Group 1's 1-minute `Live Energy`.
