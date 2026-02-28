# Treadmill FIT Corrector

Утилита корректирует дистанцию в FIT-файле (беговая дорожка, без GPS) по скоростям для каждого `lap`.

## Что делает

- читает `lap`-ы из FIT
- для каждого `lap` считает целевую дистанцию по скорости дорожки и времени круга
- при `--blend 1.0` выставляет точную целевую дистанцию каждого `lap` по скорости дорожки
- масштабирует приросты `record.distance` внутри круга, сохраняя «шершавость» графика скорости
- правит `lap.total_distance` и `session.total_distance`
- пересчитывает CRC и сохраняет валидный FIT

## Запуск

```bash
python3 treadmill_fit_corrector.py input.fit output.fit --speeds-kmh 9.5,10.0,10.5 --blend 1.0
```

Где:
- `--speeds-kmh` — скорости дорожки по кругам (`lap`) в том же порядке, как в файле
- количество скоростей должно совпадать с количеством `lap`
- `--blend 1.0` — точное совпадение дистанции каждого `lap` с целью дорожки
- `--blend 0.0` — без изменений
- `--speed-strategy invalidate` — рекомендовано для Strava (очистить speed-поля и считать темп по distance/time)
- `--speed-strategy recompute` — пересчитать speed-поля из новых distance/time
- `--trim-idle-start` — обрезать паузу в начале тренировки
- `--trim-idle-end` — обрезать паузу в конце тренировки
- `--lap-edge-stabilize-sec 8` — стабилизировать первые/последние секунды каждого lap
- `--lap-edge-blend 0.75` — сила стабилизации краёв lap
- `--lap-uniform-blend 0.35` — дополнительное выравнивание внутри lap против «треугольников» в Strava
- `--lap-spike-blend 0.2` — мягко подавлять точечные пики/провалы внутри lap

Диагностика по `record` (удобно для разбора артефактов в Strava):

```bash
python3 treadmill_fit_corrector.py input.fit output.fit --speeds-kmh 9.5,10.0,10.5 --blend 1.0 --speed-strategy invalidate --trim-idle-start --trim-idle-end --lap-edge-stabilize-sec 8 --lap-edge-blend 0.75 --lap-uniform-blend 0.35 --lap-spike-blend 0.2 --debug-csv debug.csv
```

## Примечания

- Расчёт рассчитан на тренировку на дорожке без GPS.
- Утилита патчит дистанции в существующей структуре файла и не пересобирает FIT с нуля.
- Если в файле нестандартные/редкие поля или экзотическая структура сообщений, лучше проверить результат на копии.

## Простой UI

```bash
streamlit run streamlit_app.py
```

Дальше в браузере:
- загружаешь FIT
- вводишь скорости по каждому `lap`
- жмёшь кнопку коррекции
- смотришь предпросмотр графика скорости «до/после»
- скачиваешь готовый файл

## Deploy (Streamlit Community Cloud)

1. Залей проект в GitHub (достаточно `streamlit_app.py`, `treadmill_fit_corrector.py`, `requirements.txt`, `README.md`).
2. Открой [share.streamlit.io](https://share.streamlit.io/) и войди через GitHub.
3. Нажми `New app` и выбери:
   - `Repository`: твой репозиторий
   - `Branch`: `main` (или нужная ветка)
   - `Main file path`: `streamlit_app.py`
4. Нажми `Deploy`.

Локальная проверка перед деплоем:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```
