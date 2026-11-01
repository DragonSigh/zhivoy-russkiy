# Атрибуция заимствований

`scripts/lint.py` написан для этого скилла, но использует инженерные решения
и фрагменты из открытых проектов. Все лицензии разрешают использование с
атрибуцией.

## smixs/humanizer-ru (MIT)

https://github.com/smixs/humanizer-ru

- Регулярные выражения класса A (артефакты копипаста из чат-ботов:
  oaicite, turn-метки, utm/referrer, grok-карточки, gemini-цитаты,
  внутренние сноски, остатки размышлений, плейсхолдеры) перенесены из
  `scripts/lint.py` этого проекта. smixs, в свою очередь, портит их из
  Vladimir-Human/humanizer-ru (MIT) с проверенными там fixtures.
- Стем-эвристика повторов глагольных основ в соседних предложениях
  (`verb_stems`, суффиксальный фильтр) - упрощённый порт оттуда же.
- Идея вердикта по severity и гейта через exit-код.

## ilyautov/humanizer-ru (MIT)

https://github.com/ilyautov/humanizer-ru

- Идея жанрового глушения маркеров (легитимные для регистра сигналы не
  считаются дефектом) - `GENRE_MUTE`.
- Модель скоринга «100 минус штрафы с потолками, плотность на 100 слов»
  и полосы 85/60.
- Регулярное выражение детекта латиницы внутри кириллического слова.

## ormeilu/avoid-ai-writing-russian (MIT)

https://github.com/ormeilu/avoid-ai-writing-russian

- Формат правила-триады (имя, шаблон, подсказка замены): hint печатается
  в каждой находке.
- Идея плотностных порогов для слов двойного назначения.

## comol/Humanizer_RU (MIT)

https://github.com/comol/Humanizer_RU

- Принцип zero-dependency (чистая стандартная библиотека).
- unless-подход: зачин ловится только в начале предложения, метки-ярлыки
  («Плохо:», «Хорошо:») не считаются обрубками.

## unslop - MohamedAbdallah-14/unslop (MIT)

https://github.com/MohamedAbdallah-14/unslop

- Бюджетные пороги (жирный на N слов) и структурные метрики ритма
  (монотонность, отсутствие коротких предложений).
- Урок из их бага: сплит предложений без латинского lookahead, иначе
  кириллица не разделяется.

## ZeroSlop - manavmishra/ZeroSlop (MIT)

https://github.com/manavmishra/ZeroSlop

- Идея двунаправленного факт-замка (`--fidelity`): извлечь множества
  фактов из исходника и правки, диффнуть в обе стороны, потеря = ошибка.

## conorbronsdon/avoid-ai-writing (MIT)

https://github.com/conorbronsdon/avoid-ai-writing

- Идея раздельных весов для категорий находок: структурные дефекты
  штрафуются поштучно, маркеры — по плотности.

## vale — vale-cli/vale (MIT)

https://github.com/vale-cli/vale

- Юникод-безопасные границы слов `(?<!\w)…(?!\w)` вместо `\b` там,
  где это критично.

## pymorphy3 (MIT) и razdel (MIT)

https://github.com/no-plagiarism/pymorphy3 — морфологический анализатор
(OpenCorpora), https://github.com/natasha/razdel — токенизация и сегментация
предложений для русского. Используются как опциональный слой (`pip install
pymorphy3 razdel`): повторы глаголов по леммам, цепочки номинализаций, падежное
управление, сегментация предложений. Словарь pymorphy3-dicts-ru — снимок
OpenCorpora (корпус в общественном достоянии, словарь распространяется свободно).

## Контакты скилла

Правила категорий B/K/T составлены по справочникам самого скилла
(`references/slovar-i-normy.md`, `references/perevod-i-kalki.md`,
`references/telegrafnyy-stil.md`) - это оригинальный материал.
