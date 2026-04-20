from flask import Flask, request, jsonify
import mlflow.pyfunc
import pandas as pd

app = Flask(__name__)


mlflow.set_tracking_uri("http://localhost:5000")
model = mlflow.pyfunc.load_model("models:/model_random_forest@prod")

@app.route("/predict", methods=["POST"])
def predict():
    data = request.get_json()
    df = pd.DataFrame([data])
    prediction = int(model.predict(df)[0])
    return jsonify({"prediction": prediction})

if __name__ == "__main__":
    app.run(debug=True, port=5001)
