# DeepFilterNet3 model

Модель нейросетевого шумодава речи DeepFilterNet3 (Rikorose/DeepFilterNet, MIT).
Скачана с https://raw.githubusercontent.com/Rikorose/DeepFilterNet/main/models/DeepFilterNet3.zip
Используется через `deepFilter -m presets/deepfilternet/DeepFilterNet3`.

Питон-зависимости (совместимые версии, важно): deepfilternet==0.5.6,
torch==2.0.1, torchaudio==2.0.2 (свежий torchaudio убрал torchaudio.backend и
ломает импорт). Ставятся в workflow, ~60 с, CPU. Скорость: 30 с звука ≈ 2 с.
