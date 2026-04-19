import streamlit as st
import pandas as pd
import requests

st.title("❤️‍🩹 Herramienta de problemas cardíacos")
st.write("¿Tienes probabilidad de tener problemas del corazón?")

age = st.slider("Age", 0, 100, 30)
gender = st.radio("Gender", ["Male", "Female"])
sex = 0 if gender == "Male" else 1
resting_bp = st.number_input("Resting Blood Pressure", 0, 250)
cholesterol = st.number_input("Cholesterol", 0, 600)
fasting_bs = st.radio("Fasting Blood Sugar >120", ["Yes", "No"])
fasting_bs_val = 1 if fasting_bs == "Yes" else 0
max_hr = st.number_input("Maximum Heart Rate", 0, 250)

# Enviar datos al servidor Flask
if st.button("Predecir"):
    payload = {
        "Age": age,
        "Sex": sex,
        "RestingBP": resting_bp,
        "Cholesterol": cholesterol,
        "FastingBS": fasting_bs_val,
        "MaxHR": max_hr
    }

    response = requests.post("http://localhost:5001/predict", json=payload)

    if response.status_code == 200:
        prediction = response.json()["prediction"]
        if prediction == 1:
            st.error("💔 Probablemente tenga problemas del corazón")
        else:
            st.success("💚 Probablemente no tenga problemas del corazón")
    else:
        st.warning("Error al contactar con la API")
