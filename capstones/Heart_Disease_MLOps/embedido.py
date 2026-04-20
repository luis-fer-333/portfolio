import streamlit as st
import pandas as pd
import mlflow.pyfunc


st.markdown("<h1 style='color: red;'>❤️‍🩹 Herramienta de problemas cardíacos</h1>", unsafe_allow_html=True)
st.write("¿Tienes probabilidad de tener problemas cardíacos?")


age = st.slider("Age", min_value=0, max_value=100, value=30)

gender = st.radio("Gender", ["Male", "Female"])
sex = 0 if gender == "Male" else 1

resting_bp = st.number_input("Resting Blood Pressure", min_value=0, max_value=250, step=1)
cholesterol = st.number_input("Cholesterol", min_value=0, max_value=600, step=1)

fasting_bs = st.radio("Fasting Blood Sugar", ["Yes", "No"])
fasting_bs_val = 1 if fasting_bs == "Yes" else 0

max_hr = st.number_input("Maximum Heart Rate", min_value=0, max_value=250, step=1)


input_data = pd.DataFrame([[
    age, sex, resting_bp, cholesterol, fasting_bs_val, max_hr
]], columns=["Age", "Sex", "RestingBP", "Cholesterol", "FastingBS", "MaxHR"])

mlflow.set_tracking_uri("http://localhost:5000")

model = mlflow.pyfunc.load_model("models:/model_random_forest@prod")


prediction = model.predict(input_data)[0]


if prediction == 1:
    st.markdown("💔 <span style='color:red'>**Probablemente tenga problemas del corazón**</span>", unsafe_allow_html=True)
else:
    st.markdown("💚 <span style='color:green'>**Probablemente NO tendrá problemas del corazón**</span>", unsafe_allow_html=True)
