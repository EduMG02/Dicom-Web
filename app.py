# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.utils import secure_filename
import os
from dotenv import load_dotenv
load_dotenv()
import boto3
from pymongo import MongoClient
from datetime import datetime
import pydicom
from io import BytesIO

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')

# MongoDB Atlas
MONGO_URI = os.environ.get('MONGO_URI')
client = MongoClient(MONGO_URI)
db = client.tuBase
coleccion_archivos = db.archivos
coleccion_usuarios = db.usuarios

# Amazon S3
S3_BUCKET = os.environ.get('S3_BUCKET')
s3 = boto3.client(
    's3',
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
)

ALLOWED_EXTENSIONS = {'dcm'}

def archivo_permitido(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form['usuario']
        password = request.form['password']
        if usuario == 'admin' and password == '123':
            session['usuario'] = usuario
            return redirect(url_for('index'))
        flash('Credenciales inválidas')
    return render_template('login.html')

@app.route('/', methods=['GET', 'POST'])
def index():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        archivos = request.files.getlist('archivos')
        for archivo in archivos:
            if archivo and archivo.filename != '':
                if not archivo_permitido(archivo.filename):
                    flash(f'Solo se permiten archivos DICOM (.dcm), archivo {archivo.filename} no válido.')
                    return redirect(url_for('index'))

                filename = secure_filename(archivo.filename)
                contenido = archivo.read()
                try:
                    ds = pydicom.dcmread(BytesIO(contenido), force=True)
                    paciente = ds.get("PatientName", "Desconocido")
                    patient_id = ds.get("PatientID", "Desconocido")
                except Exception as e:
                    paciente = "Error al leer"
                    patient_id = "Error al leer"

                archivo.seek(0)
                s3.upload_fileobj(archivo, S3_BUCKET, filename)
                enlace = f"https://{S3_BUCKET}.s3.amazonaws.com/{filename}"
                coleccion_archivos.insert_one({
                    'usuario': session['usuario'],
                    'nombre': filename,
                    'fecha': datetime.utcnow(),
                    'paciente': str(paciente),
                    'patient_id': str(patient_id),
                    'url': enlace
                })
        flash('Archivos DICOM subidos a S3 y registrados en MongoDB')

    archivos = list(coleccion_archivos.find({}))  # Mostrar todos los archivos (o filtrar por usuario si quieres)
    return render_template('index.html', archivos=archivos, usuario=session['usuario'])


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

import base64
import numpy as np
from PIL import Image
import io

@app.route('/ver/<filename>')
def ver(filename):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    # Descargar archivo DICOM desde S3 a memoria
    archivo_memoria = io.BytesIO()
    try:
        s3.download_fileobj(S3_BUCKET, filename, archivo_memoria)
        archivo_memoria.seek(0)
        ds = pydicom.dcmread(archivo_memoria, force=True)
    except Exception as e:
        flash('No se pudo cargar el archivo DICOM')
        return redirect(url_for('index'))

    # Extraer metadatos
    def safe_get(tag):
        return str(ds.get(tag, 'N/A'))

    metadatos = {
        'PatientName': safe_get("PatientName"),
        'PatientID': safe_get("PatientID"),
        'PatientBirthDate': safe_get("PatientBirthDate"),
        'PatientSex': safe_get("PatientSex"),
        'StudyInstanceUID': safe_get("StudyInstanceUID"),
        'StudyDate': safe_get("StudyDate"),
        'StudyTime': safe_get("StudyTime"),
        'StudyDescription': safe_get("StudyDescription"),
        'SeriesNumber': safe_get("SeriesNumber"),
        'SeriesDescription': safe_get("SeriesDescription"),
        'InstanceNumber': safe_get("InstanceNumber"),
        'Modality': safe_get("Modality"),
        'Rows': safe_get("Rows"),
        'Columns': safe_get("Columns"),
    }

    # Convertir imagen DICOM a PNG base64 para mostrar en la web
    try:
        # ds.pixel_array es un numpy array con la imagen
        arr = ds.pixel_array
        # Normalizar imagen a 0-255
        arr = (arr - arr.min()) / (arr.max() - arr.min()) * 255
        arr = arr.astype('uint8')

        im = Image.fromarray(arr)
        buffer = io.BytesIO()
        im.save(buffer, format="PNG")
        encoded_img = base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception:
        encoded_img = None

    return render_template('ver.html', metadatos=metadatos, imagen=encoded_img, filename=filename)


@app.route('/eliminar/<filename>', methods=['POST'])
def eliminar_archivo(filename):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    try:
        # Eliminar archivo de S3
        s3.delete_object(Bucket=S3_BUCKET, Key=filename)

        # Eliminar entrada en MongoDB
        resultado = coleccion_archivos.delete_one({'nombre': filename})

        if resultado.deleted_count > 0:
            flash(f'Archivo {filename} eliminado correctamente.')
        else:
            flash(f'Archivo {filename} no encontrado en la base de datos.')
    except Exception as e:
        flash(f'Error al eliminar el archivo: {str(e)}')

    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)
