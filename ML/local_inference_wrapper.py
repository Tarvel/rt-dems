import numpy as np
from datetime import datetime
import joblib
import ai_edge_litert.interpreter as tflite

class LocalEdgeForecaster:
    def __init__(self, tflite_path='te_gru_true_edge.tflite', lgb_path='lightgbm_baseline.pkl', scaler_path='scaler.joblib'):
        print("Booting Master-Slave AI Hub...")
        self.scaler = joblib.load(scaler_path)
        self.lgb_model = joblib.load(lgb_path)
        
        self.interpreter = tflite.Interpreter(model_path=tflite_path)
        self.interpreter.allocate_tensors()
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()
        
        self.current_w = 0.5
        self.current_b = 0.0
        self.uncertainty_margin_kw = 0.15 
        print("Hybrid Edge Models Loaded Successfully!")

    def build_features_and_predict(self, raw_data_dict):
        # 1. Parse Time
        dt = datetime.strptime(raw_data_dict['timestamp'], "%Y-%m-%d %H:%M:%S")
        hour = dt.hour
        day_of_week = dt.weekday()
        is_weekend = 1 if day_of_week >= 5 else 0
        
        # 2. Mathematical Time Embeddings
        hour_sin = np.sin(2 * np.pi * hour / 24)
        hour_cos = np.cos(2 * np.pi * hour / 24)
        day_sin = np.sin(2 * np.pi * day_of_week / 7)
        day_cos = np.cos(2 * np.pi * day_of_week / 7)
        month_sin = np.sin(2 * np.pi * dt.month / 12)
        month_cos = np.cos(2 * np.pi * dt.month / 12)

        # 3. Handle the Scaler 
        temp_env_array = np.zeros((1, 4))
        temp_env_array[0, 1] = raw_data_dict['temperature_c']
        temp_env_array[0, 2] = raw_data_dict['humidity']
        temp_env_array[0, 3] = raw_data_dict['lux']
        scaled_env = self.scaler.transform(temp_env_array)
        scaled_temp = scaled_env[0, 1]
        scaled_humidity = scaled_env[0, 2]
        scaled_lux = scaled_env[0, 3]

        temp_lag_array = np.zeros((4, 4))
        temp_lag_array[0, 0] = raw_data_dict['lag_1h']
        temp_lag_array[1, 0] = raw_data_dict['lag_2h']
        temp_lag_array[2, 0] = raw_data_dict['lag_3h']
        temp_lag_array[3, 0] = raw_data_dict['lag_24h']
        scaled_lags = self.scaler.transform(temp_lag_array)
        scaled_lag_1h = scaled_lags[0, 0]
        scaled_lag_2h = scaled_lags[1, 0]
        scaled_lag_3h = scaled_lags[2, 0]
        scaled_lag_24h = scaled_lags[3, 0]

        # 4. Build the Final 17-Feature Array
        raw_array = np.array([[
            scaled_temp, scaled_humidity, scaled_lux, raw_data_dict['occupancy'],
            hour, day_of_week, is_weekend,
            hour_sin, hour_cos, day_sin, day_cos, month_sin, month_cos,
            scaled_lag_1h, scaled_lag_2h, scaled_lag_3h, scaled_lag_24h
        ]], dtype=np.float32)

        # 5. Inference
        pred_lgb_scaled = float(self.lgb_model.predict(raw_array)[0])
        
        input_data = raw_array.reshape(self.input_details[0]['shape']) 
        self.interpreter.set_tensor(self.input_details[0]['index'], input_data)
        self.interpreter.invoke() 
        pred_gru_scaled = float(self.interpreter.get_tensor(self.output_details[0]['index'])[0][0])
        
        # 6. Mean Prediction (Scaled) & Inverse Transform
        mean_scaled = (self.current_w * pred_gru_scaled) + ((1 - self.current_w) * pred_lgb_scaled) + self.current_b
        
        inverse_array = np.zeros((1, 4))
        inverse_array[0, 0] = mean_scaled
        mean_kw = float(self.scaler.inverse_transform(inverse_array)[0, 0])
        
        # 7. Add safety margin for Group 3 SSR logic
        upper_bound_kw = mean_kw + self.uncertainty_margin_kw
        
        return {
            "mean_prediction_kw": round(mean_kw, 3),
            "upper_bound_kw": round(upper_bound_kw, 3)
        }
