#!/usr/bin/env python3
"""Линтер живого русского языка для скилла zhivoy-russkiy.

Детерминированная проверка чистовика по ярусам скилла: канцелярит, кальки,
типографика, телеграфность, ритм. Логика и замысел (ярусы 1-3) остаются модели.

Использование:
    python3 scripts/lint.py файл.md | -            # текстовый отчёт
    python3 scripts/lint.py файл --json            # отчёт для агента
    python3 scripts/lint.py файл --genre ui        # post | letter | doc | ui
    python3 scripts/lint.py чистовик --before исх  # факт-замок правки
    python3 scripts/lint.py файл --morph           # профиль против зон живых редакций
    python3 scripts/lint.py --series каталог/      # гейт разнообразия серии
    python3 scripts/lint.py --self-test            # включая golden-пары

Категории (все WARN, кроме артефактов):
  A  артефакты и мусор      ERROR: копипаст из чат-ботов, гомоглифы, плейсхолдеры
  B  канцелярит (ярус 5)    отглагольные, связки, шелуха, шум; оценки - плотностью
  K  кальки (ярус 8)        подстрочник, «для сохранения», ложные друзья
  T  типографика (ярус 9)   кавычки, тире, числа, наращения, висячие предлоги
  G  телеграфность (ярус 6) обрубки, «X - это Y», повторы зачинов и глаголов
  M  ритм и метрики         стены предложений, монотонность, жирный

Скор: 100 минус штрафы с потолками по категориям.
Полосы: 85+ clean / 60-84 review / ниже 60 rewrite.
Exit: 0 - готово; 1 - класс A или потеря факта (или скор ниже --gate).

Заимствования и лицензии: THIRD_PARTY_NOTICES.md.

Опциональный слой морфологии: pip install pymorphy3 razdel - включает точную
сегментацию предложений, повторы глаголов по леммам, цепочки номинализаций
и падежное управление (согласно приказУ, не приказА). Без пакетов ядро
работает на чистом re с эвристиками.
"""
import datetime
import json
import re
import sys
from pathlib import Path

# --- опциональный морфологический слой (pip install pymorphy3 razdel) ---
# Нет пакетов - ядро работает на чистом re; есть - включаются точные проверки:
# сегментация razdel, повторы по леммам, цепочки номинализаций, падежное
# управление. Замеры: 21К слов/с, 17 МБ на диске, Python 3.12 из коробки.
MORPH = RAZDEL = None
try:
    import pymorphy3 as _pymorphy3
    import razdel as _razdel
    MORPH = _pymorphy3.MorphAnalyzer()
    RAZDEL = _razdel
except Exception:
    pass

# ---------------------------------------------------------------- A артефакты
# Артефакты копипаста и утечек генерации. Класс ERROR: блокируют выдачу
# независимо от скора и жанра.
ARTIFACTS = [
    ("A control-символ", re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")),  # NUL/BEL/ANSI-управляющие символы
    ("A oaicite-сноска", re.compile(r":contentReference\[oaicite:\d+\]|oai_citation:\d+‡|\boaicite:\d+")),
    ("A turn-метка", re.compile(r"\bturn\d+(?:search|file|fetch|image|news|video|ref)\d+|citeturn")),
    ("A utm/referrer чат-бота", re.compile(r"utm_source=(?:chatgpt|copilot)\.com|referrer=grok\.com")),
    ("A grok-карточка", re.compile(r"grok_card://|grok_render_citation_card_json|<grok-card\b")),
    ("A gemini-цитата", re.compile(r"vertexaisearch\S*grounding-api-redirect|\[cite_start\]|\[cite:\s*\d+|\[span_\d+\]")),
    ("A внутренняя сноска", re.compile(r"【\d+†[^】]*】|\]\(sandbox:/mnt/data/")),
    ("A остаток размышлений", re.compile(r"</?think>")),
    ("A perplexity-upload", re.compile(r"ppl-ai-file-upload")),
    ("A placeholder", re.compile(r"INSERT_SOURCE_URL|PASTE_\w+_URL_HERE|\bURL_HERE\b|\b20\d\d-XX-XX\b")),
    ("A PUA-метка", re.compile(r"[-]")),   # невидимые приватные кодпоинты
    # служебные маркеры рассуждений, протёкшие в пользовательский текст
    ("A спецтокен модели", re.compile(r"<\|[a-z_]+\|>|### STOP ###|Thought Process[:\s]|<\|begin_of_box\|>")),
    # дегенеративный цикл: одно слово 5+ раз подряд. Только кириллица:
    # латинские повторы в цитатах и поэзии - цитата-приём, не дефект
    ("A цикл повтора", re.compile(r"(\b[а-яё]{2,}\b)([ ,]?\1){4,}", re.I)),
    # полноширинная пунктуация и цифры CJK-локалей (，。１００％) - артефакт
    # смешения раскладок или сырой генерации; на стороне источника лечится
    # NFKC-нормализацией
    ("A полноширинный символ", re.compile("[\u3000-\u303F\uFF00-\uFFEF]")),
]
CODE_STRIP = re.compile(r"```.*?```|`[^`\n]+`", re.S)   # артефакт в бэктиках цитируют, не копипастят
EMOJI_CH = "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️\U0001F3FB-\U0001F3FF]"
ZERO_WIDTH = re.compile(r"[​‌⁠﻿]|(?<!%s)‍|‍(?!%s)" % (EMOJI_CH, EMOJI_CH))
# слово со смесью кириллицы и латиницы («браузерe», «прилoжение») - след
# подмены букв и копипаста; гомоглиф ищется в любой позиции слова
MIXED_WORD = re.compile(r"(?<![\w-])[А-Яа-яЁёA-Za-z]*(?:[А-Яа-яЁё][A-Za-z]|[A-Za-z][А-Яа-яЁё])[А-Яа-яЁёA-Za-z]*(?![\w-])")
# чужие письменности: в русском тексте они не легитимны нигде - ни в слове,
# ни сами по себе (спам-водяные знаки иероглифами, греческие подмены букв)
FOREIGN_SCRIPTS = (
    "Ͱ-Ͽἀ-῿"                    # греческий
    "԰-֏"                                  # армянский
    "֐-׿"                                  # иврит
    "؀-ۿ"                                  # арабский
    "ऀ-ॿ"                                  # деванагари
    "฀-๿"                                  # тайский
    "Ⴀ-ჿ"                                  # грузинский
    "぀-ヿ"                                  # хирагана и катакана
    "㐀-䶿一-鿿豈-﫿"        # CJK иероглифы
    "\U00020000-\U0002A6DF"                          # CJK расширение B
    "가-힯"                                  # хангыль
)
FOREIGN_IN_WORD = re.compile(rf"(?<=[а-яёА-ЯЁa-z0-9])[{FOREIGN_SCRIPTS}]")
FOREIGN_CHAR = re.compile(rf"[{FOREIGN_SCRIPTS}]+")  # серия символов = одна находка, иначе спам по каждой букве

# ------------------------------------------------------------- B канцелярит
# Формат записи: (имя, regex, подсказка замены). Маркеры, не запреты:
# находка требует решения редактора, а не автозамены.
BUREAUCRACY = [
    ("отглагольное", re.compile(
        r"\bосуществ\w+|\bпроизвед[её]н[аоы]?\b|\bпроизвод(?:ится|иться|едено|ятся)\b|"
        r"\bпроведени\w+|\bреализаци\w+|\bнаграждени\w+", re.I),
     "верни глагол и деятеля: «проверили», а не «произведена проверка»"),
    ("связка-пара", re.compile(
        r"\bв рамках\b|\bв целях\b|\bв связи с (?:тем что)?\b|\bна сегодняшний день\b|"
        r"\bв настоящее время\b|\bна текущий момент\b|\bв данный момент\b|\bв данном случае\b", re.I),
     "«в проекте», «чтобы», «сейчас» - без канцелярской обёртки"),
    ("безличное", re.compile(
        r"\bотсутствует возможность\b|\bне имеется\b|\bимеет место\b|\bне допускается наличие\b|"
        r"\bпроизошл[ои] улучшени\w*|\bпредоставля\w+ возможность\b", re.I),
     "назови деятеля и действие: «пока нельзя», «мы добавили»"),
    ("словесная алгебра", re.compile(
        r"\bтот факт, что\b|\bфакт отсутствия\b|\bотметить факт\b|\bряд моментов\b|\bряд вопросов\b|"
        r"\bопределённое количество\b|\bв южном направлении\b|\bхлебобулочные изделия\b", re.I),
     "выброси подмену: «нет гласности», «многое мешает», «к югу», «хлеб»"),
    # указательное «данный»: после предлога почти всегда существительное
    # («по данным», «с данными», «по большим данным») - такие случаи пропускаем
    ("данный", re.compile(
        r"(?<!по )(?<!с )(?<!из )(?<!за )(?<!от )(?<!на )(?<!о )(?<!об )(?<!к )(?<!в )"
        r"(?<!те )(?<!эти )(?<!всех )(?<!большим )(?<!большими )(?<!больших )"
        r"\bданн(?:ый|ая|ое|ого|ому|ым|ом|его|ему|им|ем)\b", re.I),
     "«этот» вместо казённого «данный»; проверь, нужно ли указательное вообще"),
    ("иноязычная шелуха", re.compile(
        r"\bпролонгир\w+|\bлимитир\w+|\bаннулир\w+|\bактивизир\w+|\bконтактир\w+|"
        r"\b(?:прийти|пришли|придём|придем) к консенсусу\b|\bмесседж\w+|\bозвуч\w+ данные\b|"
        r"\bпозиционир\w+ себя\b|\bнавигир\w+|\bделикатн\w+ стирк\w+|\bфункционал(?:а|у|ом|е|ы)?\b", re.I),
     "есть точное русское: «продлить», «отменить», «сообщить данные», «обязанности»"),
    ("наукообразное", re.compile(
        r"\bактуализир\w+|\bверифицир\w+|\bдиверсифицир\w+|\bимплементир\w+|\bнивелир\w+|"
        r"\bперманентн\w+|\bпревалир\w+|\bредуцир\w+|\bстагнаци\w+|\bпарадигм\w+|\bсинерги\w+", re.I),
     "обычное слово передаёт смысл яснее"),
    ("вводный шум", re.compile(
        r"\bбезусловно,|\bразумеется,|\bочевидно,|\bкак известно\b|\bпо сути,|\bв принципе,|"
        r"\bследует отметить\b|\bнеобходимо подчеркнуть\b|\bважно отметить\b|"
        r"\bнеобходимо учитывать, что\b|\bсоответственно,|\bесли (?:коротко|вкратце)\b|\bбез лишних слов\b", re.I),
     "выбрось вводное - фраза не теряет ничего"),
    ("пустой зачин", re.compile(
        r"(?:^\s*|(?<=[.!?…]\s))(?:в современном мире|в наше время|в наши дни|в последнее время)\b,?", re.I),
     "начни с факта, действия или проблемы читателя (ловится только в начале предложения)"),
]
# Плотностные правила: (имя, regex, порог на 100 слов, подсказка).
# Порог - сигнал, не приговор: оценивать скоплением, не одиночной находкой.
B_DENSITY = [
    ("усилители", re.compile(
        r"\bочень\b|\bкрайне\b|\bчрезвычайно\b|\bмаксимально\b|\bневероятно\b|"
        r"\bабсолютно\b|\bсовершенно\b|\bпредельно\b|\bисключительно\b|\bбуквально\b|\bреально\b", re.I),
     4, "усилитель без опоры на факт - нажим; попробуй точное слово"),
    ("пустые оценки", re.compile(
        r"\bкачественн\w*|\bэффективн\w*|\bуникальн\w*|\bинновационн\w*|\bоптимальн\w*|"
        r"\bпремиальн\w*|\bэлитн\w*|\bкультов\w*|\bвысок\w+ уровен\w+|\bширок\w+ спектр\w*|"
        r"\bбогат\w+ опыт\w*|\bиндивидуальн\w+ подход\w*|\bкомплексн\w+ решени\w*", re.I),
     2, "за оценкой должен стоять факт или цифра"),
    ("туманные", re.compile(
        r"\bнекотор\w+\b|\bнекий\b|\bкакой-то\b|\bте или иные\b|\bразличн\w+\b", re.I),
     3, "если группу можно назвать точнее - назови"),
    ("является", re.compile(r"\bявля(?:ется|ются|лась|лся|лось|лись)\b", re.I),
     1, "«X — метод» вместо «X является методом»"),
]

# ------------------------------------------------------------------ K кальки
CALQUES = [
    ("продал копии", re.compile(
        r"\b(?:игра|релиз|альбом|студия) продал(?:[аи]|\b)|\bпродал\w* \d+ копи\w+", re.I),
     "по-русски покупают: «игру купили 100 раз», «релиз разошёлся тиражом»"),
    ("пожизненная выручка", re.compile(r"\bпожизненн\w+ выручк\w+", re.I),
     "lifetime revenue = «выручка за всё время продаж»"),
    ("для-отглагольное", re.compile(
        r"\bдля (?:сохранения|просмотра|оплаты|загрузки|установки|получения|отправки|проверки|поиска|ввода)\b", re.I),
     "калька «to save»: «чтобы сохранить», «чтобы оплатить»"),
    ("уведомлены", re.compile(r"\bвы будете уведомлены\b", re.I),
     "«мы пришлём уведомление» - живой деятель вместо пассива"),
    ("вызовы", re.compile(r"\bсталкивается с рядом вызовов\b|\bв ответ на вызовы\b|\bнесмотря на эти вызовы\b", re.I),
     "challenge здесь - «сложные задачи», не «вызовы»"),
    ("экспертиза-как-experience", re.compile(r"\bмноголетняя экспертиза\b|\bэкспертиза в области\b|\bнаша экспертиза\b", re.I),
     "expertise = «опыт, квалификация»; экспертиза - это экспертиза"),
    ("драматический", re.compile(r"\bдраматическ\w+ (?:рост|падение|изменени\w+|повышени\w+|снижени\w+)\b", re.I),
     "dramatic = «резкий, разительный»"),
    ("декада", re.compile(r"\bпоследн\w+ декад\w+|\bза декаду\b|\bдекаду назад\b", re.I),
     "decade = «десятилетие»; декада - десять дней"),
    ("identity-личность", re.compile(
        r"\bличност\w+ (?:игрока|для перехода)|\bвладени\w+ личность\w|\bпотратить\w* [^,.]{1,20} на личность\b", re.I),
     "identity = «единый профиль / аккаунт», а не «личность»"),
    ("пожизненная метрика", re.compile(r"\bпожизненн\w+ (?:конверси|продаж)\w*|\bпродажи за всю жизнь\b", re.I),
     "lifetime = «за всё время продаж», не «пожизненный»"),
    ("shipping-слой", re.compile(r"\bслой (?:выпуска|публикации)\b|\bспредшит\w*|\bперсистентн\w+", re.I),
     "«инфраструктура релиза», «таблица», «сохранность данных» - без подстрочника"),
    ("буквальный образ", re.compile(
        r"\bдёргать рычаги\b|\bкорабельный слой\b|\bверхняя щепка\b|\bзапуски без головы\b|\bучёный по данным\b", re.I),
     "построй русский образ заново: «рабочие приёмы», «инструменты выпуска», «дата-сайентист»"),
    ("ложные друзья", re.compile(
        r"\bаккуратн\w+ (?:подсчёт|перевод|расчёт|измерени\w+)\b|\bагрессивн\w+ (?:маркетинг|рост|стратегия)\b|"
        r"\bамбициозн\w+ (?:план|цель|проект)\b", re.I),
     "accurate = «точный», aggressive = «напористый», ambitious = «смелый, масштабный»"),
    # judgment-калька: «ИИ берёт рутину, но не суждение» - жаргон дата-сферы,
    # по-русски точнее «решения», «интерпретация», «выводы»
    ("суждение-judgment", re.compile(
        r"\b(?:не )?суждени[ея]\b(?=[^.!?]{0,30}(?:за человеком|остан|решени|рутина|рутину|ИИ|AI))|"
        r"\b(?:бер[её]т|взять|забрать|заменить|делегировать)[^.!?]{0,30}\bсуждени[ея]\b", re.I),
     "калька judgment: «суждение» = умозаключение; здесь точнее «решения» или «интерпретация»"),
]

# -------------------------------------------------------------- T типографика
TYPOGRAPHY = [
    ("прямые кавычки", re.compile(r"[\wа-яё“»] *\n?\"|\" *\n?[\wа-яё]"),
     "в русском тексте внешние кавычки - «ёлочки», вложенные - „лапки“"),
    ("дефис как тире", re.compile(r"\w+ - \w+"),
     "между подлежащим и сказуемым - тире «—», не дефис"),
    ("пробел у знака", re.compile(r"\w +[,.;:!?]|[,.!?]{2}(?![)\"])\b"),
     "пробел - после знака препинания, не перед"),
    ("восклиц-серия", re.compile(r"[!?]{2,}"),
     "одного знака достаточно"),
    ("наращение", re.compile(r"\d-(?:ый|ого|ому|ыми|ых|ая|ой)\b|\b2-ух\b"),
     "после гласной одна буква: «5-й», «3-м»; после согласной - две: «5-го»"),
    ("число без разрядов", re.compile(r"(?<!№)(?<!\d)(?<!\d )\d{5,}(?!\d)(?!.? \d{4})"),
     "многозначное число делят по три цифры: «8 201 794»; годы и номера - не делят"),
    ("английские тысячи", re.compile(r"(?<!\d)\d{1,3},\d{3}\b"),
     "в русском тексте тысячи разделяют пробелом: «$1 000», «50 000»"),
    ("дробь через точку", re.compile(r"(?<!\d)(?<![A-Za-z] )\d+\.\d+(?!\d*\.)"),
     "десятичная дробь в русском тексте - через запятую: «1,07»; точка - только версии и код"),
    ("пробел после знака", re.compile(r"[а-яё],[а-яёА-ЯЁ]"),
     "после запятой нужен пробел"),
    ("цифра в начале", re.compile(r"(?:^|\.\s)\d+ [а-яё]+"),
     "предложение не начинают с цифры - напиши словом"),
    ("висячий предлог", re.compile(r"\b(?:на|в|о|об|от|до|для|с|со|по|к|у|при) ?:"),
     "пред перечнем не оставляй висячий предлог: «делятся на такие виды:»"),
]
# смешение е/ё: если текст держит «ё», одиночные «е» в тех же словах - разнобой
YO_WORDS = re.compile(r"\bеще\b|\bчетк\w+|\bтрех\w*\b|\bчетырех\w*\b|\bсерьезн\w*\b")

# склейка «X это Y» БЕЗ тире - системный дефект ИИ-черновиков (канон скилла:
# перед «это» между подлежащим и сказуемым нужно тире, либо живой глагол)
THIS_GLUE = re.compile(r"\b([а-яёА-ЯЁ][а-яёА-ЯЁ-]{2,}) это ([а-яёА-ЯЁ][а-яёА-ЯЁ-]{2,})")
THIS_BEFORE_STOP = {"что", "кто", "как", "где", "зачем", "почему", "чем", "чему",
                    "всё", "все", "если", "когда", "сделать", "значит"}
THIS_AFTER_STOP = {"значит", "было", "будет", "стало", "касается", "так"}


def this_glue_findings(sents, findings):
    """«МойТаймер это приложение» без тире. Глагол перед «это» («делает это
    прямо») - дополнение, не склейка: с морфологией фильтруем по POS."""
    for s in sents:
        for m in THIS_GLUE.finditer(s):
            w1, w2 = m.group(1).lower(), m.group(2).lower()
            if w1 in THIS_BEFORE_STOP or w2 in THIS_AFTER_STOP:
                continue
            # «X — это Y» с тире легитимно
            if s[max(0, m.start(1) - 2):m.start(1)].rstrip().endswith(("—", "-")):
                continue
            if MORPH is not None:
                # связка засчитывается только при X=NOUN и Y=NOUN/ADJ:
                # «Тогда это казалось», «Вот это наша встреча» - не определения
                pos1 = {p.tag.POS for p in MORPH.parse(w1)}
                pos2 = {p.tag.POS for p in MORPH.parse(w2)}
                if VERB_POS & pos1 or VERB_POS & pos2 or "NPRO" in pos1:
                    continue
                if not ({"NOUN"} & pos1):
                    continue
                if not ({"NOUN", "ADJF", "ADJS", "PRTF"} & pos2):
                    continue
            elif not m.group(1)[0].isupper():
                continue  # без морфа - только имена продуктов с заглавной
            findings.append(("WARN", "G", "это без тире", 0, m.group(0),
                             "«X — это Y» с тире или живой глагол: «МойТаймер работает на телефоне»"))


# ------------------------------------------------------------ G телеграфность
# подводка перед двоеточием: «суть в следующем:», «главное здесь:» -
# признак неназванного главного (раздел скилла о двоеточиях)
COLON_SETUP = re.compile(
    r"\b(?:суть в следующем|главное здесь|вот в чём (?:дело|штука|фишка)|"
    r"самое (?:интересное|главное|важное)|лучшая часть|фишка в том)\s*:", re.I)
THIS_IS = re.compile(r"(?:^|[.!?…]\s)[^.!?…]{1,60}? — это \w")
DIGIT_START = re.compile(r"^\s*\d")
VERB_SUFFIX = re.compile(r"(ует|яет|ает|еет|ит|ат|ят|ют|ал|ял|ил|ел|ся|сь|ть)$")
STOPWORD_OPENERS = {"и", "а", "но", "в", "на", "с", "по", "что", "это", "как", "так",
                    "для", "если", "когда", "чтобы", "мы", "вы", "он", "она", "они", "не", "из", "за", "от", "до", "о", "к", "у"}

ABBREV = re.compile(r"т\. ?е\.|т\. ?к\.|т\. ?д\.|и\. ?т\. ?п\.|и\. ?др\.|напр\.|см\.|ср\.|стр\.|г\.|гг\.|руб\.|коп\.|млн\.|млрд\.|тыс\.|прим\.|рис\.|табл\.|№")
STRIP_MD = re.compile(r"```.*?```|`[^`\n]+`|https?://\S+|\*\*|__|«|»", re.S)
LINT_VERSION = "1.0.0"
CALIB_VERSION = "v3: ru-bench 01.11.2026, смесь n=155 (эпохи 28/55/72), CI в analysis/calib-v3.md"

FRONTMATTER_END = re.compile(r"\A---\n.*?\n---\n", re.S)
# служебная разметка: HTML-теги и markdown-ссылки - не проза.
# Ссылку заменяем словом-плейсхолдером: вырезание в пустоту оставило бы
# пробел перед знаком препинания и порождало фантом «пробел у знака».
HTML_TAG = re.compile(r"<[^>\n]+>")
MD_LINK = re.compile(r"!?\[[^\]\n]*\]\([^)\n]*\)|https?://\S+")


def strip_frontmatter(text):
    return FRONTMATTER_END.sub("", text, count=1)


def fm_offset(text):
    m = FRONTMATTER_END.match(text)
    return m.group(0).count("\n") if m else 0

# ------------------------------------------------------- жанровое глушение
# жанровое глушение: маркеры, законные для регистра, не штрафуются.
# Списки редакторские - по жанровым разделам скилла.
GENRE_MUTE = {
    "ui":     {"cat:telegraph"},                 # UI-текст телеграфен по жанру
    "doc":    {"rule:это-определение"},           # в документации определения уместны
    "letter": {"rule:это-определение", "rule:вводный шум"},
    "post":   set(),
}

def verdict_for(score):
    if score >= 85:
        return "clean"
    if score >= 60:
        return "review - посмотри warnings кластерами"
    return "rewrite - тексту нужна переработка, не точечные правки"


PENALTY = {"A": (12, 45), "B": (2.0, 30), "K": (2.5, 20), "T": (5.0, 20),
           "G": (6.0, 32), "M": (10.0, 36)}
# Структурные дефекты (T/G/M) - поштучные веса: дефект есть независимо от длины
# текста. Маркеры канцелярита и кальков (B/K) штрафуются плотностью на 100 слов.
WEIGHT = {
    "T": {"прямые кавычки": 4, "дефис как тире": 4, "наращение": 4,
          "число без разрядов": 5, "цифра в начале": 4, "пробел у знака": 3,
          "пробел после знака": 3, "висячий предлог": 4, "восклиц-серия": 4,
          "разнобой е/ё": 2, "одиночная ё": 2, "управление падежом": 5,
          "проверь слово": 2, "английские тысячи": 3, "дробь через точку": 4,
          "день недели vs дата": 3},
    "G": {"цепочка обрубков": 5, "это-определение": 6, "пересказ таблиц": 6,
          "повтор глагола": 4, "повтор зачина": 4, "цепочка номинализаций": 6,
          "плотность двоеточий": 6, "двоеточие-подводка": 4, "это без тире": 5},
    "M": {"текст из обрубков": 15, "монотонный ритм": 18, "стены предложений": 10,
          "нет коротких": 10, "жирный перебор": 6},
}
CAT_NAMES = {"A": "артефакт", "B": "канцелярит", "K": "калька", "T": "типографика и нормы",
             "G": "телеграфность", "M": "ритм", "F": "факт-замок"}


# ------------------------------------------------ морфометрия (профиль текста)
# Калибровка: 40 оригинальных статей 8 русскоязычных изданий (2001-2026),
# квантильные зоны p5-p95 по каждой метрике. Гейт - не по средним: статья
# должна попадать в зону спектра живых редакций, серия - держать межстатейный
# разброс (CV >= половины эталонного), иначе конвейер выдаёт статьи «на подбор».
# В ядро зон отобраны только метрики с AUC > 0.6 на корпусах сравнения.
# Дрейф эпох сопоставим с главным эффектом (d 0.6-1.3), поэтому корпус обязан
# смешивать годы: зона, собранная только на свежих текстах, выбрасывает
# 11 из 17 живых текстов 2018-2022. Пополнение корпуса = пересборка зон
# и новая версия калибровки (CALIB).

# Исключены из зон: verb% и mattr100 (знак обратный - конвейер глагольнее и
# лексически разнообразнее живых текстов, зона ничего не отсекает),
# rhythm_sigma (конфаундер средней длины, rho 0.76), noun%/prcl%/npro%
# (дублируют замыкание). Любая зона - справка, не вердикт: зоны меряют
# усреднённый жанр; сырая генерация попадает в них в 92% случаев.
MORPH_CALIB = {  # зоны посчитаны на смеси корпусов (расширение n=101 -> n=155)
    # под-зоны docs/translated/prosvet имеют собственные профили
    # (docs: part% 2.6-3.5, short8 0.44+) и этой смесью не калибруются
    "part%": (0.69, 3.57),    # n=155: межверсионный сдвиг границ меньше ширины CI
    "advb%": (2.81, 7.70),
    "wlen": (5.36, 6.74),     # CI верхней 18% ширины - метрика устойчива
    "short8": (0.06, 0.45),
    "ell": (0.00, 0.18),
    "p90p50": (1.47, 2.30),
}
# Гейт разнообразия серии калиброван на реальных сериях, не на смесях корпусов:
# 4 эталонные серии изданий против 4 конвейерных серий.
#   CV noun%: эталонные 9.9-19.8, конвейерные 4.0-6.4 -> порог 8.0
#             (устойчив: LOSO 16/16, пересечение классов в двухуровневом
#              бутстрапе 15%)
#   CV verb%: эталонные 10.5-18.7, конвейерные 5.7-8.6 -> порог 9.5
#             (слабее: пересечение 46%, но единственный критерий, ловящий
#              сырые конвейерные серии)
# Ошибки (бутстрап B=10000): ложные срабатывания на живых сериях ~30% при n=10
# и ~40% при n=5; мощность по конвейерным 99.6%. Детектор конвейерной
# штамповки, не «ИИ-текста вообще»: класс калибровки узкий (4+4 серии),
# пополнение эталонных серий сужает доверительные интервалы сильнее правки порога.
SERIES_CV_MIN = {"noun%": 8.0, "verb%": 9.5}
SERIES_CV_INFO = ("wlen", "advb%")
RHYTHM_FLOOR = 6.5      # внутри-текстовая сигма длин предложений; справочно

MORPH_GROUPS = {  # pymorphy3: tag.POS, глаголы = VERB + INFN
    "verb": {"VERB", "INFN"}, "noun": {"NOUN"}, "adj": {"ADJF", "ADJS"},
    "advb": {"ADVB"}, "part": {"PRTF", "PRTS", "GRND"}, "prep": {"PREP"},
    "conj": {"CONJ"}, "prcl": {"PRCL", "INTJ"},
    # местоимения в числителе: иначе диалоговый текст занижает остальные доли
    "npro": {"NPRO"},
}
WORD_RE = re.compile(r"^[а-яёА-ЯЁ][а-яё-]*$")


def _mattr(lemmas, window=100):
    if len(lemmas) < window:
        return len(set(lemmas)) / len(lemmas) if lemmas else 0.0
    scores = [len(set(lemmas[i:i + window])) / window
              for i in range(len(lemmas) - window + 1)]
    return sum(scores) / len(scores)


def morph_skip_reason(text):
    """Почему профиль недоступен: пакеты / длина / кириллица."""
    if MORPH is None or RAZDEL is None:
        return "нет pymorphy3/razdel (pip install pymorphy3 razdel)"
    paras = prose_paragraphs(text)
    words = [t.text for p in paras for t in RAZDEL.tokenize(p) if WORD_RE.match(t.text)]
    if len(words) < 100:
        return f"прозы меньше 100 кириллических слов ({len(words)}) - профиль не определён"
    if sum(1 for w in words if MORPH.parse(w.lower())[0].tag.POS) < 50:
        return "почти нет распознаваемых русских слов"
    return None


def morph_profile(text):
    """Морфометрия статьи: доли POS (от распознанных), mattr, длина слова,
    внутри-текстовая сигма ритма. None без pymorphy3/razdel или на тексте
    <100 слов (причина - morph_skip_reason)."""
    if MORPH is None or RAZDEL is None:
        return None
    paras = prose_paragraphs(text)
    words = [t.text for p in paras for t in RAZDEL.tokenize(p) if WORD_RE.match(t.text)]
    if len(words) < 100:
        return None
    counts = {g: 0 for g in MORPH_GROUPS}
    lemmas, n, total_len = [], 0, 0
    for w in words:
        total_len += len(w.replace("-", ""))
        parse = MORPH.parse(w.lower())[0]
        pos = parse.tag.POS
        if pos:
            n += 1
            for g, members in MORPH_GROUPS.items():
                if pos in members:
                    counts[g] += 1
                    break
        lemmas.append(parse.normal_form)
    # ритм: сигма коррелирует со средней длиной предложения (rho 0.76),
    # поэтому длиннохвостость меряют:
    lens = []
    for p in paras:
        for s in RAZDEL.sentenize(p):
            k = sum(1 for t in RAZDEL.tokenize(s.text) if WORD_RE.match(t.text))
            if k >= 2:
                lens.append(k)
    import statistics
    lens_sorted = sorted(lens)
    d = n or 1
    prof = {f"{g}%": round(100 * counts[g] / d, 2) for g in MORPH_GROUPS}
    prof["mattr100"] = round(_mattr(lemmas), 3)      # справка: парафраз-детектор
    prof["wlen"] = round(total_len / len(words), 2)
    if len(lens) >= 10:
        def q(p):
            h = (len(lens_sorted) - 1) * p
            lo, hi = int(h), min(int(h) + 1, len(lens_sorted) - 1)
            return lens_sorted[lo] + (lens_sorted[hi] - lens_sorted[lo]) * (h - lo)
        prof["short8"] = round(sum(1 for x in lens if x < 8) / len(lens), 3)
        prof["ell"] = round(sum(1 for x in lens if x < 4) / len(lens), 3)
        prof["p90p50"] = round(q(.9) / max(q(.5), 1), 2)
        prof["rhythm_sigma"] = round(statistics.pstdev(lens), 2)  # справка
    prof["_n_words"] = len(words)
    return prof


def morph_check(text):
    """Профиль + статус каждой метрики против зоны эталонов (ok/low/high)."""
    prof = morph_profile(text)
    if prof is None:
        return None, []
    rows = []
    for met, (lo, hi) in MORPH_CALIB.items():
        v = prof.get(met)
        if v is None:
            continue
        status = "ok" if lo <= v <= hi else ("low" if v < lo else "high")
        rows.append((met, v, f"{lo}-{hi}", status))
    return prof, rows


def series_morph_check(files):
    """Advisory-гейт разнообразия серии. Три слоя: CV noun%/verb%,
    дословный шаблон (ловит серию с рубриками, где CV обманчиво высок
    из-за кочующих дословных предложений) и медианы зон."""
    import statistics
    texts = [(f, Path(f).read_text(encoding="utf-8")) for f in files]
    profs = [p for p in (morph_profile(t) for _, t in texts) if p]
    if len(profs) < 5:
        return "INSUFFICIENT", [
            f"статей с профилем {len(profs)}, нужно не меньше 5 - гейт неприменим"]
    out, fails = [], 0
    mode = "FAIL" if len(profs) >= 10 else "WEAK"

    def cv_of(met):
        xs = [p[met] for p in profs if p.get(met) is not None]
        if len(xs) < 5:
            return None
        m = statistics.mean(xs)
        return 100 * statistics.pstdev(xs) / m if m else 0.0

    for met, limit in SERIES_CV_MIN.items():
        cv = cv_of(met)
        if cv is None:
            continue
        ok = cv >= limit
        fails += 0 if ok else 1
        out.append(f"{'ok  ' if ok else 'FAIL'} CV {met:7} {cv:5.1f}% (порог {limit}%)")
    for met in SERIES_CV_INFO:
        cv = cv_of(met)
        if cv is not None:
            out.append(f"     CV {met:7} {cv:5.1f}% (справочно)")
    # дословный шаблон: предложения >=6 слов, повторяющиеся в >=3 статьях серии
    from collections import Counter
    sent_files = {}
    for f, t in texts:
        for para in prose_paragraphs(t):
            for s in split_sentences(para):
                if _SERVICE_SENT.search(s):
                    continue
                # сквозные блоки подписи, призыва и ссылок на книгу -
                # легитимная навигация издания, не шаблон
                if re.search(r"подпишитесь|это был фрагмент|читайте (?:в |далее|вопрос|также)"
                             r"|по ссылке|наш (?:телеграм|канал)|из (?:моей )?книги"
                             r"|книга называется|моя новая книга|вышла моя книга"
                             r"|покупайте (?:ее|её)|ozon|wildberries|litres", s, re.I):
                    continue
                s2 = " ".join(s.lower().split())
                if len(s2.split()) >= 6:
                    sent_files.setdefault(s2, set()).add(f)
    boiler = {s: fs for s, fs in sent_files.items() if len(fs) >= 3}
    if boiler:
        fails += 1
        sample = sorted(boiler, key=lambda s: -len(boiler[s]))[:3]
        out.append(f"FAIL дословный шаблон: {len(boiler)} предложений >=6 слов"
                   f" кочуют по >=3 статьям; пример: «{sample[0][:70]}…»")
    # медианы зон: >=3 из 6 метрик вне зоны - сигнал конвейерности
    n_out_zone = 0
    for met, (lo, hi) in MORPH_CALIB.items():
        xs = [p[met] for p in profs if p.get(met) is not None]
        if len(xs) < 5:
            continue
        med = statistics.median(xs)
        ok = lo <= med <= hi
        n_out_zone += 0 if ok else 1
        out.append(f"     медиана серии {met:9} {med:6.2f} [{lo}-{hi}]"
                   f" {'ok' if ok else 'вне зоны'}")
    if n_out_zone >= 3:
        fails += 1
        out.append(f"FAIL профиль серии: {n_out_zone} медиан из {len(MORPH_CALIB)}"
                   f" вне зон живых редакций")
    verdict = ("FAIL" if fails else "PASS") if mode == "FAIL" else \
              ("WEAK-FAIL" if fails else "WEAK-PASS")
    return verdict, out


# ------------------------------------------------------------------- движок
def line_view(text, offset=0):
    """Строки для пословного скана B/K/T: без кода, HTML и markdown-служебного."""
    raw = CODE_STRIP.sub(lambda m: "\n" * m.group(0).count("\n") if "\n" in m.group(0) else "код", text)
    raw = HTML_TAG.sub("", raw)
    raw = MD_LINK.sub("ССЫЛКА", raw)
    return enumerate(raw.splitlines(), 1 + offset)


def prose_paragraphs(text):
    """Прозаические абзацы: без заголовков, таблиц, списков, цитат, кода, URL."""
    clean = STRIP_MD.sub(lambda m: " " * len(m.group(0)), strip_frontmatter(text))
    paras, cur = [], []
    for line in clean.splitlines():
        s = line.strip()
        if not s:
            if cur:
                paras.append(" ".join(cur))
                cur = []
        elif re.match(r"^(#|\||[-*+]\s|\d+\.\s|>)", s):
            if cur:
                paras.append(" ".join(cur))
                cur = []
        else:
            cur.append(s)
    if cur:
        paras.append(" ".join(cur))
    return paras


def split_sentences(paragraph):
    if RAZDEL is not None:
        return [s.text.strip() for s in RAZDEL.sentenize(paragraph) if s.text.strip()]
    protected = ABBREV.sub(lambda m: m.group(0).replace(".", "․"), paragraph)
    sents = re.split(r"(?<=[.!?…])\s+", protected)
    return [s.replace("․", ".").strip() for s in sents if s.strip()]


VERB_POS = {"INFN", "VERB", "PRTS", "PRTF", "GRND"}
NOM_SUFFIXES = ("ние", "тие", "ство", "ция")


STOP_LEMMAS = {"быть", "мочь", "стать", "стоить", "стоять", "деть", "стечь"}


def verb_lemmas(sentence):
    """Леммы глаголов; без морфологии - стем-эвристика."""
    if MORPH is None:
        stems = set()
        for w in re.findall(r"[а-яё]{5,}", sentence.lower()):
            if VERB_SUFFIX.search(w):
                stems.add(VERB_SUFFIX.sub("", w)[:6])
        return {s for s in stems if len(s) >= 4}
    out = set()
    for w in re.findall(r"[а-яё]{4,}", sentence.lower()):
        # прочтения объединяются с фильтром по score: отсекает фантомные парсы
        # («дела» -> VERB «деть» со score 0.003, «стек» -> «стечь»)
        out |= {p.normal_form for p in MORPH.parse(w)
                if p.tag.POS in VERB_POS and (p.score or 0) >= 0.05}
    return {s for s in out if len(s) >= 4} - STOP_LEMMAS


def nominal_chain(sentence):
    """Компактная цепочка отглагольных существительных: «интеграция, тестирование
    и развёртывание» - номинализации подряд, разделённые только запятыми и «и/или».
    Разбросанные по предложению («обещание … устройству … интеграциями») - не цепочка."""
    if MORPH is None:
        return []

    def is_nom(w):
        return any(p.tag.POS == "NOUN" and p.normal_form.endswith(NOM_SUFFIXES)
                   for p in MORPH.parse(w))

    tokens = re.findall(r"[а-яё]+|[,.:;«»—]", sentence.lower())
    sep = {",", "и", "или"}
    chain, out = [], []
    for t in tokens:
        if is_nom(t):
            chain.append(t)
        elif t not in sep:
            if len(chain) >= 3:
                out.append(chain)
            chain = []
    if len(chain) >= 3:
        out.append(chain)
    return out


def word_count(text):
    return len(re.findall(r"[а-яёa-z0-9]+(?:-[а-яёa-z0-9]+)*", text.lower()))


_SERVICE_SENT = re.compile(
    r"\d{1,2}[. ]\w+[. ]?\d{4}|\d+\s+фото|^Год:|^Жанр:|^Разработчик:|^Издатель:"
    r"|размест\w*\s+реклам|соцсет|поделиться|репостнуть|все права|©|18\+"
    r"|twitter|telegram|youtube|vk |vk$|rss|tiktok|discord|дзен|информаци\w+$"
    r"|^\d+ (лекци|выпуск|минут|част|урок|вопрос|глав)"
    r"|^редакция|^контакты|политика конф|фс77|источник:|^комментарии"
    r"|следите за новыми постами", re.I)


def lint(text, genre=None):
    findings = []   # (severity, cat, rule, line, excerpt, hint)
    # нормализация CRLF/BOM: без неё ломается срез frontmatter и растут фантомные T
    text = text.replace("\r\n", "\n").lstrip("﻿")
    mute = GENRE_MUTE.get(genre, set())
    muted_cat = {m[4:] for m in mute if m.startswith("cat:")}
    muted_rule = {m[6:] for m in mute if m.startswith("rule:")}
    words = max(1, word_count(strip_frontmatter(text)))

    def muted(rule, cat):
        return rule in muted_rule or cat in muted_cat

    # A: по сырому тексту без frontmatter (URL нужны для utm-детекта), без бэктиков
    off = fm_offset(text)
    body = strip_frontmatter(text)
    raw = CODE_STRIP.sub(lambda m: "\n" * m.group(0).count("\n"), body)
    for i, line in enumerate(raw.splitlines(), 1 + off):
        for rule, rx in ARTIFACTS:
            for m in rx.finditer(line):
                ctx = line[max(0, m.start() - 25):m.end() + 25].strip()
                findings.append(("ERROR", "A", rule, i, ctx, "текст скопирован из чат-бота? вычисти след"))
        if ZERO_WIDTH.search(line):
            findings.append(("WARN", "A", "нулевая ширина", i, line.strip()[:60],
                             "невидимый символ (бывает от CMS) - проверь источник"))
        for m in MIXED_WORD.finditer(line):
            ctx = line[max(0, m.start() - 20):m.end() + 20].strip()
            findings.append(("WARN", "A", "гомоглиф", i, ctx,
                             "латинская буква в русском слове (или кириллическая в латинском)"))
        in_word_spans = [(m.start(), m.end()) for m in FOREIGN_IN_WORD.finditer(line)]
        for s, e in in_word_spans:
            ctx = line[max(0, s - 20):e + 20].strip()
            findings.append(("WARN", "A", "гомоглиф чужого алфавита", i, ctx,
                             "символ чужой письменности внутри слова (CJK, греческий и пр.)"))
        for m in FOREIGN_CHAR.finditer(line):
            if any(s <= m.start() < e for s, e in in_word_spans):
                continue  # буква внутри слова уже поймана гомоглифом - без двойного штрафа
            ctx = line[max(0, m.start() - 20):m.end() + 20].strip()
            findings.append(("WARN", "A", "чужой алфавит", i, ctx,
                             "иероглиф или буква чужой письменности в русском тексте - проверь, не водяной ли знак"))
    # B/K/T: построчный скан без frontmatter, HTML и markdown-служебного
    yo_hits = len(re.findall(r"ё", body))
    first_yo_line = next((ln for ln, l2 in line_view(body) if "ё" in l2), None)
    for i, line in line_view(body):
        # строка-атрибут MDX/JSX-компонента (icon="bot") - не проза
        if re.match(r'^\s*[\w-]+="[^"]*"\s*$', line) or \
           re.match(r"^\s*(#|\||```)", line) or not line.strip():
            continue
        scan = re.sub(r"^\s*[>+*]\s", "  ", line)
        for rule, rx, hint in BUREAUCRACY:
            if muted(rule, "B"):
                continue
            for m in rx.finditer(scan):
                if rule == "вводный шум":
                    before = scan[:m.start()]
                    if before.count("«") > before.count("»"):
                        continue  # прямая речь - голос героя, не автора
                ctx = scan[max(0, m.start() - 25):m.end() + 25].strip()
                findings.append(("WARN", "B", rule, i, ctx, hint))
        for rule, rx, hint in CALQUES:
            if muted(rule, "K"):
                continue
            for m in rx.finditer(scan):
                ctx = scan[max(0, m.start() - 25):m.end() + 25].strip()
                findings.append(("WARN", "K", rule, i, ctx, hint))
        for rule, rx, hint in TYPOGRAPHY:
            if muted(rule, "T") or rule == "прямые кавычки" and scan.strip().startswith(("«", "„")):
                continue
            # дата-стемпы и регистрационные номера - вёрстка, не текст
            if rule == "цифра в начале" and (
                    re.match(r"^\s*\d{1,2} [а-яё]+ \d{4}", scan) or
                    re.match(r"^\s*\d+ [а-яё]+( [а-яё]+)?\s*$", scan) or
                    re.fullmatch(r"\s*\d+\s*[а-яё]+(?:\s*\d+\s*[а-яё]+)+\s*", scan) or
                    (scan.strip() and scan.strip()[0].isdigit() and not scan.rstrip().endswith((".", "!", "?")))):
                # склейки спанов вёрстки («4 лекции2 материала») - не текст
                continue
            if rule == "число без разрядов" and re.search(r"№|ФС", scan):
                continue
            for m in rx.finditer(scan):
                ctx = scan[max(0, m.start() - 25):m.end() + 25].strip()
                findings.append(("WARN", "T", rule, i, ctx, hint))
        if yo_hits >= 3 and YO_WORDS.search(scan):
            findings.append(("WARN", "T", "разнобой е/ё", i, scan.strip()[:60],
                             "в тексте с «ё» эти слова пишутся через «ё»: «ещё», «чётко»"))
    if 0 < yo_hits < 3 and YO_WORDS.search(body) and first_yo_line is not None:
        # политика «без ё» - не разнобой. Подозрение на опечатку: одна «ё» рядом
        # со словами из YO_WORDS; без них «ё» легитимна (частичная политика)
        yo_line = next(l2 for ln, l2 in line_view(body) if "ё" in l2)
        findings.append(("WARN", "T", "одиночная ё", first_yo_line,
                         yo_line.strip()[:60],
                         "в тексте без «ё» одна «ё»: опечатка или политика издания"))

    for rule, rx, hint in (  # «за счёт»: позволяется только при глаголе урезания/расширения
        ("за счёт", re.compile(r"\bза счёт\b", re.I),
         "«за счёт» = ценой урезания другого; о положительной причине - «благодаря»"),
        ("собирательное + часы", re.compile(r"\b(?:двое|трое) часов\b", re.I),
         "о двух отсчётах времени: «два отсчёта / две шкалы», не «двое часов» (калька two clocks)"),
    ):
        if muted(rule, "K"):
            continue
        for i2, scan2 in line_view(body):
            for m in rx.finditer(scan2):
                ctx = scan2[max(0, m.start() - 25):m.end() + 25].strip()
                findings.append(("WARN", "K", rule, i2, ctx, hint))

    # плотностные B-правила: порог на 100 слов, отчёт одним суммарным сигналом
    for rule, rx, per100, hint in B_DENSITY:
        if muted(rule, "B"):
            continue
        hits = rx.findall(body)
        limit = words / 100 * per100
        if len(hits) > max(limit, 1):
            sample = ", ".join(h[:3] for h in [str(x) for x in hits[:4]])
            findings.append(("WARN", "B", f"плотность: {rule}", 0,
                             f"{len(hits)} вхождений на {words} слов ({sample})",
                             f"порог ~{per100} на 100 слов. {hint}"))

    # G: телеграфность по прозаическим предложениям; служебные строки изданий
    # (футеры, стемпы, карточки) из ритма исключены
    SERVICE_SENT = _SERVICE_SENT
    sents, owners = [], []
    for para in prose_paragraphs(text):
        for s in split_sentences(para):
            if len(s.split()) <= 7 and SERVICE_SENT.search(s):
                continue
            sents.append(s)
            owners.append(para)
    lengths = [len(s.split()) for s in sents]

    if not muted_cat & {"telegraph"}:
        # двоеточия: плотность в прозе (законны только перечень, цитата,
        # расшифровка слова - всё остальное склейка самостоятельных мыслей)
        prose = " ".join(prose_paragraphs(text))
        colons = prose.count(":")
        if colons > max(2, words / 100 * 3):
            findings.append(("WARN", "G", "плотность двоеточий", 0,
                             f"{colons} двоеточий на {words} слов прозы",
                             "двоеточие - для перечня, цитаты или расшифровки; склейку мыслей перестрой союзом или вторым предложением"))
        # склейка «X это Y» без тире
        this_glue_findings(sents, findings)
        # подводка: «суть в следующем:», «главное здесь:»
        for m in COLON_SETUP.finditer(prose):
            ctx = prose[max(0, m.start() - 30):m.end() + 30].strip()
            findings.append(("WARN", "G", "двоеточие-подводка", 0, ctx,
                             "скажи сразу, в чём суть, без подводки и двоеточия"))
        # три и более обрубков <=5 слов подряд (сигнал повторяется каждые +2)
        run = 0
        for j, (s, ln) in enumerate(zip(sents, lengths)):
            if ln <= 5 and not DIGIT_START.match(s) and "?" not in s and not s.endswith(":") \
                    and re.search(r"(?:\b(?:он|она|они|мы|вы|я|ты|это|вс[её]|там|потом)|"
                                  r"(?:ет|ёт|ут|ют|ит|ат|ят|ил|ыл|ал|ел|ать|ять|ить|ешь|ишь|аю|ую|ся|сь)\b)", s):
                # номинативные метки («Сужение.», «Сто пятьдесят.») - жанровая
                # стенография; ловим только обрубки фраз
                run += 1
                if run >= 3 and run % 2 == 1:
                    findings.append(("WARN", "G", "цепочка обрубков", 0,
                                     " → ".join(sents[max(0, j - 2):j + 1]),
                                     "восстанови глаголы и связи, объедини предложения"))
            else:
                run = 0
        # «X — это Y» плотностью
        this_is = [s for s in sents if THIS_IS.search(s)]
        if not muted("это-определение", "G") and len(this_is) > max(1, words / 300):
            findings.append(("WARN", "G", "это-определение", 0,
                             f"{len(this_is)} определений через «— это»",
                             "объясни, что предмет делает и чем ограничен, а не вешай этикетку"))
        # пересказ таблиц: соседние предложения с цифры
        for j in range(len(sents) - 1):
            if DIGIT_START.match(sents[j]) and DIGIT_START.match(sents[j + 1]):
                findings.append(("WARN", "G", "пересказ таблиц", 0,
                                 f"{sents[j][:50]} / {sents[j+1][:50]}",
                                 "сначала вывод, затем цифры для доказательства"))
        # повторы глаголов в соседних предложениях: по леммам (морфология)
        # или стем-эвристикой. Прямая речь и цитаты исключены: повторы там -
        # голос героя, не автора
        for j in range(len(sents) - 1):
            if sents[j].lstrip().startswith(("«", "„", "— «", "—«")) or \
               sents[j + 1].lstrip().startswith(("«", "„", "— «", "—«")):
                continue
            common = verb_lemmas(sents[j]) & verb_lemmas(sents[j + 1])
            if common:
                findings.append(("WARN", "G", "повтор глагола", 0,
                                 f"«{sorted(common)[0]}…» в соседних предложениях: {sents[j+1][:50]}",
                                 "найди другой глагол или убери повтор"))
        # компактная цепочка отглагольных существительных (ярус 5)
        for s in sents:
            for chain in nominal_chain(s):
                findings.append(("WARN", "G", "цепочка номинализаций", 0,
                                 f"{len(chain)} подряд: {', '.join(chain[:4])}",
                                 "перечисление действий существительными - проверь, не пересказ ли это таблицы"))
        # повторы зачинов абзацев
        openers = [p.split()[0].lower().strip(",.!?") if p.split() else "" for p in prose_paragraphs(text)]
        for j in range(len(openers) - 1):
            if (openers[j] == openers[j + 1] and len(openers[j]) >= 4
                    and openers[j] not in STOPWORD_OPENERS):
                findings.append(("WARN", "G", "повтор зачина", 0,
                                 f"два абзаца с «{openers[j]}…»",
                                 "смени зачин второго абзаца"))

    # падежное управление (только с морфологией): согласно/благодаря + дательный
    if MORPH is not None:
        for m in re.finditer(r"\b(?:согласно|благодаря|вопреки|навстречу)\s+([а-яё]{3,})",
                             body.lower()):
            w = m.group(1)
            if not MORPH.word_is_known(w):
                continue
            cases = {p.tag.case for p in MORPH.parse(w)}
            if "gent" in cases and "datv" not in cases:
                findings.append(("WARN", "T", "управление падежом", 0,
                                 f"{m.group(0)} - после «{m.group(0).split()[0]}» нужен дательный падеж",
                                 "«согласно приказУ», «благодаря помощИ друзей»"))
        # несоответствие дня недели дате в скобках: «к воскресенью (22.08)»
        # при 22.08 = суббота - типовая ошибка генерации дат
        for m in re.finditer(
                r"\b(понедельни\w*|вторни\w*|сред\w*|четвер\w*|пятниц\w*|суббот\w*|воскресен\w*)\s*\((\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\)",
                body.lower()):
            stem = next(s for s in ("понедельни", "вторни", "сред", "четвер", "пятниц", "суббот", "воскресен")
                        if m.group(1).startswith(s))
            wd = {"понедельни": 0, "вторни": 1, "сред": 2, "четвер": 3,
                  "пятниц": 4, "суббот": 5, "воскресен": 6}[stem]
            d_, mo, y_ = int(m.group(2)), int(m.group(3)), m.group(4)
            try:
                y_ = int(y_) if y_ else datetime.date.today().year
                if datetime.date(y_, mo, d_).weekday() != wd:
                    findings.append(("WARN", "T", "день недели vs дата", 0,
                                     m.group(0) + f" - {d_:02d}.{mo:02d}.{y_} это " +
                                     ["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"][datetime.date(y_, mo, d_).weekday()],
                                     "сверь день недели с календарём - типовая ошибка моделей"))
            except ValueError:
                pass
        for w, hits in typo_suspects(text):
            findings.append(("WARN", "T", "проверь слово", 0,
                             f"«{w}» - не в словаре; похоже на «{hits[0]}»",
                             "проверь написание: возможно, опечатка"))

    # M: ритм
    if len(lengths) >= 5 and sum(lengths) / len(lengths) <= 6:
        findings.append(("WARN", "M", "текст из обрубков", 0,
                         f"средняя длина предложения {sum(lengths)/len(lengths):.1f} слова",
                         "телеграфный ритм: восстанови глаголы и связки, объедини предложения"))
    if len(lengths) >= 8:
        diffs = [abs(x - y) for x, y in zip(lengths, lengths[1:])]
        mean_diff = sum(diffs) / len(diffs)
        if mean_diff < 4:
            findings.append(("WARN", "M", "монотонный ритм", 0,
                             f"средняя разница длин соседних предложений {mean_diff:.1f} слова (живой текст: 6+)",
                             "чередуй длинные и короткие предложения"))
        if len(lengths) >= 10 and not any(l <= 8 for l in lengths):
            findings.append(("WARN", "M", "нет коротких", 0,
                             "ни одного предложения до 8 слов",
                             "короткое предложение ставит акцент"))
        long_share = sum(1 for l in lengths if l > 25) / len(lengths)
        if long_share >= 0.25:
            findings.append(("WARN", "M", "стены предложений", 0,
                             f"{long_share:.0%} предложений длиннее 25 слов",
                             "разбей длинные предложения или выброси лишнее"))
    bold_count = len(re.findall(r"\*\*[^*\n]+\*\*", strip_frontmatter(text)))
    if words >= 200 and bold_count > words / 200 + 1:
        findings.append(("WARN", "M", "жирный перебор", 0,
                         f"{bold_count} жирных на {words} слов (норма ~{max(1, words // 200)})",
                         "выделяй только главное, примерно один спан на 200 слов"))

    # скор: B/K - плотность на 100 слов; A/T/G/M - поштучно с весами правил.
    score = 100.0
    # Поштучные штрафы на длинном тексте бьют непропорционально (28 находок
    # на 9000 слов эквивалентны 5 на 1500): нормировка на референсные 1500 слов.
    # Глобальные свойства целого текста (монотонность, стены предложений)
    # не нормируются: монотонный текст из 810 одинаковых предложений
    # иначе получал 95/clean
    GLOBAL_RULES = {"монотонный ритм", "нет коротких", "текст из обрубков",
                    "стены предложений"}
    len_factor = min(1.0, 1500 / max(words, 1))
    for cat, (per, cap) in PENALTY.items():
        n = len([f for f in findings if f[1] == cat])
        if n == 0:
            continue
        if cat in ("B", "K"):
            # плотность на 100 слов, но знаменатель не ниже 50 слов:
            # на 2-5 словах один маркер давал -100 и уносил короткий
            # интерфейсный текст в rewrite за одно «произведите оплату»
            score -= min(cap, per * n / max(words, 50) * 100)
        else:
            raw = 0.0
            for f in findings:
                if f[1] != cat:
                    continue
                w = WEIGHT.get(cat, {}).get(f[2], per)
                raw += w if f[2] in GLOBAL_RULES else w * len_factor
            score -= min(cap, raw)
    score = max(0, round(score))
    errors = [f for f in findings if f[0] == "ERROR"]
    verdict = verdict_for(score)
    return findings, score, verdict, errors, words, lengths


# ------------------------------------------------- серия текстов (портфель)
def edit1(a, b):
    """Отличаются одной правкой - но не флексией: правка в последних двух
    буквах почти всегда падеж («пресета/пресете», «табата/табату»)."""
    if len(a) == len(b):
        pos = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(pos) == 1 and pos[0] < len(a) - 2
    if abs(len(a) - len(b)) == 1:
        s, l = (a, b) if len(a) < len(b) else (b, a)
        i = 0
        while i < len(s) and s[i] == l[i]:
            i += 1
        return i <= len(s) - 3 and s[i:] == l[i + 1:]
    return False


def _tags(word):
    """Грамматические разборы слова: (POS, падеж, число, время) по всем парсам."""
    return {(p.tag.POS, p.tag.case, p.tag.number, p.tag.tense) for p in MORPH.parse(word)}


def _tag1(word):
    """Наиболее вероятный разбор: омонимичные парсы («счета» = и род.ед.)
    дают ложное пересечение тегов."""
    p = MORPH.parse(word)[0]
    return (p.tag.POS, p.tag.case, p.tag.number, p.tag.tense)


# формы местоимения «весь»: «все» (мн.ч.) и «всё» (ср.ед.), «всем/всём» -
# разные словоформы, а не орфографический разнобой
YO_FORM_SKIP = {"все", "всё", "всем", "всём", "всей", "всеми", "всего", "всему",
                "все", "всё"}


def yo_mismatch(w, twin):
    """Настоящий ли разнобой е/ё: у разных грамматических форм одного слова
    («счета» им.мн. vs «счёта» род.ед.) теги парсов не пересекаются."""
    if w in YO_FORM_SKIP or twin in YO_FORM_SKIP:
        return False
    if MORPH is None or not (MORPH.word_is_known(w) and MORPH.word_is_known(twin)):
        return True
    # пересечение тегов всех парсов: первый парс шатается на омонимии
    # ударения («звезды»/«звёзды»). Омоформы вроде «счета»/«счёта»
    # (им.мн. vs род.ед.) различает только контекст предложения, поэтому
    # редкий ложный WARN допустим: категория не является гейтом.
    return bool(_tags(w) & _tags(twin))


def portfolio(paths):
    """Разнобой написаний по серии файлов: е/ё-двойники и пары неизвестных
    словарю слов в одной правке друг от друга («хенгборд»/«хангборд»,
    «бёрпи»/«берпи»). Use case - сверка терминологии серии перед публикацией."""
    from collections import Counter, defaultdict
    counts, where = Counter(), defaultdict(set)
    for p in paths:
        text = strip_frontmatter(Path(p).read_text(encoding="utf-8"))
        for w in re.findall(r"[а-яё]{4,}", text.lower()):
            counts[w] += 1
            where[w].add(Path(p).name)
    findings = []
    seen = set()
    for w in sorted(counts):
        twin = w.replace("ё", "е")
        if twin != w and twin in counts and (twin, w) not in seen and yo_mismatch(w, twin):
            seen.add((w, twin))
            findings.append(("разнобой е/ё", f"«{w}» ×{counts[w]} vs «{twin}» ×{counts[twin]}",
                             sorted(where[w] | where[twin])[:3]))
    if MORPH is not None:
        unknown = [w for w in sorted(counts) if not MORPH.word_is_known(w)]
        for i, a in enumerate(unknown):
            for b in unknown[i + 1:]:
                if counts[a] < 2 or counts[b] < 2:
                    continue
                if edit1(a, b):
                    findings.append(("разнобой терминов", f"«{a}» ×{counts[a]} vs «{b}» ×{counts[b]}",
                                     sorted(where[a] | where[b])[:3]))
    return findings


def typo_suspects(text):
    """Неизвестные словарю слова, правка одной буквы которых даёт известное:
    «Приинципиальный» -> «принципиальный». Только с морфологией."""
    if MORPH is None:
        return []
    out = []
    # полные словоформы с границами: обрубок {7,14} резал «распространённая»
    for w in set(re.findall(r"(?<![\w-])[а-яё]{8,}(?![\w-])", strip_frontmatter(text).lower())):
        if MORPH.word_is_known(w):
            continue
        cands = {w[:i] + w[i + 1:] for i in range(len(w))} | {w.replace("ё", "е")}
        # замены только гласных: полноалфавитный перебор даёт ложные
        # попадания на профессиональном жаргоне
        cands |= {w[:i] + c + w[i + 1:] for i in range(1, len(w) - 1)
                  for c in "аеиоуыэюя"}
        hits = sorted(c for c in cands if len(c) >= 6 and MORPH.word_is_known(c))
        if hits:
            out.append((w, hits[:2]))
    return out


# ------------------------------------------------------------- факт-замок
MONTHS = "январ|феврал|март|апрел|мая|мае|июн|июл|август|сентябр|октябр|ноябр|декабр"
NAME_STOP = {"если", "когда", "однако", "кроме", "также", "таким", "всё", "все",
             "для", "чтобы", "благодаря", "согласно", "вопреки", "наконец", "итак"}


def extract_facts(text):
    t = strip_frontmatter(text)
    t = CODE_STRIP.sub(" ", t)
    facts = {"числа": set(), "даты": set(), "url": set(), "цитаты": set(), "имена": set()}
    # числа: нормализуем разрядные пробелы и проценты, десятичная дробь через запятую - одно число
    digits = re.sub(r"(?<=\d) (?=\d{3}\b)", "", t)
    for m in re.finditer(r"\d[\d\s]*(?:,\d+)?|\d", digits):
        x = re.sub(r"\s", "", m.group(0))
        if x.endswith(","):
            x = x[:-1]
        facts["числа"].add(x)
    for m in re.finditer(r"(\d+)\s*(?:процентов|процент|%)", digits):
        facts["числа"].add(m.group(1) + "%")
        facts["числа"].discard(m.group(1))   # голое число уже учтено как процент
    for m in re.finditer(rf"\d{{1,2}}\s+(?:{MONTHS})\w*\s+\d{{4}}", t):
        facts["даты"].add(re.sub(r"\s+", " ", m.group(0).lower()))
    for m in re.finditer(r"https?://\S+|[\w.+-]+@[\w.-]+\.\w+", t):
        facts["url"].add(m.group(0).rstrip(".,);»"))
    # цитаты: и ёлочки, и прямые кавычки (типографика меняет "х" на «х» - это не новый факт)
    for m in re.finditer(r"«([^»]+)»|„([^“]+)“|\"([^\"]+)\"", t):
        q = (m.group(1) or m.group(2) or m.group(3)).strip().lower()
        if q:
            facts["цитаты"].add(q)
    for m in re.finditer(r"(?<![.!?…] )\b([А-ЯЁ][а-яё]+) ([А-ЯЁ][а-яё]+)\b", t):
        if m.group(1).lower() not in NAME_STOP and m.group(2).lower() not in NAME_STOP:
            facts["имена"].add(f"{m.group(1)} {m.group(2)}")
    return facts


# цифра и словесное числительное - один факт («5-ый» -> «пятый», «к 8 утра» -> «к восьми»)
NUM_WORDS = {
    "1": ("один", "одна", "одно", "одну", "одним", "одном", "одного", "одной", "перв", "во-первых"),
    "2": ("два", "две", "двух", "двум", "двумя", "втор"),
    "3": ("три", "трёх", "трем", "трём", "тремя", "трет"),
    "4": ("четыре", "четырёх", "четырем", "четырьмя", "четвёрт", "четверт"),
    "5": ("пять", "пяти", "пятью", "пят"),
    "6": ("шесть", "шести", "шестью", "шест"),
    "7": ("семь", "семи", "семью", "седьм"),
    "8": ("восемь", "восьми", "восьмью", "восьм"),
    "9": ("девять", "девяти", "девятью", "девят"),
    "10": ("десять", "десяти", "десят"),
    "11": ("одиннадцат",),
    "12": ("двенадцат",),
    "15": ("пятнадцат",),
    "20": ("двадцат",),
    "25": ("двадцать пять", "двадцати пяти"),
    "30": ("тридцат",),
    "40": ("сорок", "сорока"),
    "100": ("сто", "ста", "сот"),
    "970": ("девятьсот семьдесят", "девятьсот семидесяти"),
}


def _numeral_present(after, num):
    for form in NUM_WORDS.get(num, ()):
        if re.search(rf"(?<![а-яё]){re.escape(form)}", after, re.I):
            return True
    return False
    return facts


FACT_LOST = {"числа": "потеряно число", "даты": "потеряна дата", "url": "потерян URL",
             "цитаты": "потеряна цитата", "имена": "потеряно имя"}
FACT_ADDED = {"числа": "добавлено число", "даты": "добавлена дата", "url": "добавлен URL",
              "цитаты": "добавлена цитата", "имена": "добавлено имя"}


def fact_lock(before, after):
    """Двусторонняя сверка фактов: потеря = ERROR, добавление = WARN."""
    fb, fa = extract_facts(before), extract_facts(after)
    low_after = after.lower()
    report = []
    for kind in fb:
        lost = fb[kind] - fa[kind]
        added = fa[kind] - fb[kind]
        for x in sorted(lost):
            if kind == "числа" and _numeral_present(low_after, x):
                continue  # цифра передана словом («5» -> «пятый») - факт на месте
            report.append(("ERROR", "F", FACT_LOST[kind], 0, x,
                           "правка убрала факт из исходника - верни или подтверди намеренность"))
        for x in sorted(added):
            report.append(("WARN", "F", FACT_ADDED[kind], 0, x,
                           "нового факта не было в исходнике - проверь источник"))
    return report


# ------------------------------------------------------------------ вывод
def report_text(findings, score, verdict, errors, words, lengths):
    # сводка сверху: характер находок виден до деталей
    if findings:
        by_cat = {}
        for sev, cat, *_ in findings:
            by_cat[cat] = by_cat.get(cat, 0) + 1
        order = ["A", "B", "K", "T", "G", "M", "F"]
        summary = ", ".join(f"{CAT_NAMES.get(c, c)} {n}" for c in order
                            for cc, n in by_cat.items() if cc == c)
        print(f"сводка: {len(findings)} находок - {summary}")
        print("канцелярит и артефакты правь сразу; телеграфность читай жанровым"
              " глазом (цитата, таблица, стиль издания - не дефект)\n")
    def sane(s):
        return s[:120].replace("\x1b", "^[")

    for sev, cat, rule, line, excerpt, hint in findings:
        loc = f"строка {line}" if line else "текст"
        print(f"{sev} {loc}: [{CAT_NAMES.get(cat, cat)}] {rule}: {sane(excerpt)}")
        if hint:
            print(f"       → {hint}")
    if MORPH is None or RAZDEL is None:
        print("режим: эвристики (pymorphy3/razdel недоступны - морф-проверки выключены)")
    if lengths:
        print(f"\nобъём: {words} слов, {len(lengths)} предложений, "
              f"средняя длина {sum(lengths)/len(lengths):.1f} слов")
    print(f"итого: скор {score}/100 -> {verdict}")
    if errors:
        print("ГЕЙТ НЕ ПРОЙДЕН - есть ERROR (артефакты или потеря факта).")
    return 1 if errors else 0


def self_test():
    core_n = 0
    here = Path(__file__).parent

    # канцелярит ловится, чистый - чист
    bad = "В рамках проекта было осуществлено проведение проверки корректности работы данного функционала."
    f, s, v, e, w, _ = lint(bad)
    cats = {x[1] for x in f}
    assert "B" in cats and s < 85, (cats, s)
    ok = "Мы проверили, правильно ли загрузились данные, и нашли две ошибки. Теперь всё работает."
    f, s, v, e, w, _ = lint(ok)
    assert not e and s >= 85, (f, s)

    # артефакты - ERROR
    f, s, v, e, w, _ = lint("Рынок вырос :contentReference[oaicite:0]{index=0}, детали turn0search3.")
    assert e, f

    # телеграфность
    tel = "Строка помечается ошибочной. Подсвечивается красным. Без слов строка остаётся обычной."
    f, s, v, e, w, _ = lint(tel)
    assert any(x[2] == "цепочка обрубков" for x in f), f
    f, s, v, e, w, _ = lint(tel, genre="ui")
    assert not any(x[2] == "цепочка обрубков" for x in f), "жанр ui глушит телеграфность"

    # кальки и типографика
    f, s, v, e, w, _ = lint("Нажмите кнопку для сохранения изменений. Он сказал \"привет\" и ушёл.")
    assert any(x[1] == "K" for x in f), f
    assert any(x[2] == "прямые кавычки" for x in f), f
    f, s, v, e, w, _ = lint("Вариант 5-ый подошёл. Диапазон 15000-20000 метров норма.")
    assert any(x[2] == "наращение" for x in f), f

    # ритм
    mono = " ".join(["Это предложение содержит ровно семь слов подряд."] * 12)
    f, s, v, e, w, _ = lint(mono)
    assert any(x[2] == "монотонный ритм" for x in f), f

    # факт-замок
    src = "Мы отправили 721 письмо 18 октября 2026 года, отклик 12 процентов."
    lost = "Мы отправили много писем 18 октября 2026 года, отклик хороший."
    rep = fact_lock(src, lost)
    assert any(x[0] == "ERROR" and "721" in x[4] for x in rep), rep
    added = "Мы отправили 721 письмо 18 октября 2026 года, отклик 12 процентов, плюс 400 звонков."
    rep = fact_lock(src, added)
    assert any("400" in x[4] for x in rep), rep

    # гомоглиф
    f, s, v, e, w, _ = lint("Приложение прилoжение обновилось.")  # второе слово с латинской o
    assert any(x[2] == "гомоглиф" for x in f), f
    # чужие письменности: иероглиф в слове и греческая буква отдельно
    f, s, v, e, w, _ = lint("Приложение обновилось 时 без сбоев, данныε сверены.")
    assert any("чуж" in x[2] for x in f), f

    # это-склейка без тире (глагол перед «это» - не склейка)
    f, s, v, e, w, _ = lint("МойТаймер это интервальный таймер для круговых тренировок. Отдых это половина нагрузки.")
    assert any(x[2] == "это без тире" for x in f), f
    f, s, v, e, w, _ = lint("Приложение делает это прямо на телефоне. Всё это работает само.")
    assert not any(x[2] == "это без тире" for x in f), f
    f, s, v, e, w, _ = lint("МойТаймер — это интервальный таймер.")
    assert not any(x[2] == "это без тире" for x in f), f

    # морфологический слой (проверяется, если установлен pymorphy3 + razdel)
    if MORPH is not None:
        f, s, v, e, w, _ = lint("Мы действуем согласно приказу и не меняем расписание уроков.")
        assert not any(x[2] == "управление падежом" for x in f), f
        f, s, v, e, w, _ = lint("Согласно приказа директора школа закрыта на каникулы.")
        assert any(x[2] == "управление падежом" for x in f), f
        f, s, v, e, w, _ = lint("Проведение осуществления улучшения процесса тестирования продолжается третий день.")
        assert any(x[2] == "цепочка номинализаций" for x in f), f
        f, s, v, e, w, _ = lint("Мы проверили данные вручную. Затем мы проверили отчёт целиком.")
        assert any(x[2] == "повтор глагола" for x in f), f
        f, s, v, e, w, _ = lint("Приинципиальный хвост длиннее, чем кажется.")
        assert any(x[2] == "проверь слово" for x in f), f
        f, s, v, e, w, _ = lint("Ответьте к воскресенью (22.08) и до пятницы (20.08).")
        assert any(x[2] == "день недели vs дата" for x in f), f

    # golden-пары: «плохо» всегда хуже «хорошо»
    golden_dir = here.parent / "texts" / "golden"
    if not golden_dir.is_dir():
        print("self-test: FAIL - каталог texts/golden отсутствует (битая поставка)")
        return 1
    pairs = sorted(golden_dir.glob("*.bad.md"))
    for p in pairs:
        good = p.with_name(p.name.replace(".bad.md", ".good.md"))
        bad_text, good_text = p.read_text(encoding="utf-8"), good.read_text(encoding="utf-8")
        fb, sb, _, _, _, _ = lint(bad_text)
        fg, sg, _, eg, _, _ = lint(good_text)
        assert sb < sg, f"golden {p.name}: bad {sb} >= good {sg}"
        assert sg >= 85, f"golden {p.name}: good ниже полосы clean ({sg})"
        assert not eg, f"golden {p.name}: у good есть ERROR: {eg}"
        fl = fact_lock(bad_text, good_text)
        assert not [x for x in fl if x[0] == "ERROR"], f"golden {p.name}: факт-замок ERROR: {fl}"
        long_words = len(re.findall(r"[а-яё]+", good_text, re.I))
        if long_words >= 150:
            morph_skip = morph_skip_reason(good_text)
            if not morph_skip:
                mp = morph_profile(good_text)
                assert mp is not None, f"golden {p.name}: морфометрия не построилась"
    core_n = sum(1 for l in open(__file__, encoding="utf-8") if l.strip().startswith("assert"))
    morph_n = 0 if (MORPH is None or RAZDEL is None) else 7
    mode = "morph" if morph_n else "эвристики"
    print(f"self-test: OK (режим: {mode}; ядро: {core_n}; morph-ассертов: {morph_n};"
          f" golden: {len(pairs)} пар; lint {LINT_VERSION})")
    return 0


def read_file(path):
    """Чтение файла: utf-8-sig; битые байты и отсутствие файла - CliError
    с stderr, не traceback с exit 1 (он неотличим от вердикта «текст грязный»)."""
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        raise CliError(f"{path}: файл не в UTF-8 (байт {e.start}) - сохраните в UTF-8")
    except OSError as e:
        raise CliError(f"{path}: {e.strerror or e}")


class CliError(Exception):
    pass


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    as_json = "--json" in args
    genre = None
    before_path = None
    portfolio_dir = None
    series_dir = None
    want_morph = "--morph" in args
    gate = None
    positional = []
    it = iter(args)
    try:
        for a in it:
            if a == "--genre":
                genre = next(it, None)
            elif a == "--before":
                before_path = next(it, None)
            elif a == "--portfolio":
                portfolio_dir = next(it, None)
            elif a == "--series":
                series_dir = next(it, None)
            elif a == "--gate":
                gate = int(next(it, "0") or 0)
            elif a == "-" or not a.startswith("-"):
                positional.append(a)
    except ValueError:
        print("неверное значение --gate: нужен целочисленный порог скора", file=sys.stderr)
        return 2
    if genre is not None and genre not in ("post", "letter", "doc", "ui"):
        print(f"неизвестный жанр {genre!r}: post | letter | doc | ui", file=sys.stderr)
        return 2
    if len(positional) > 1:
        print(f"лишние аргументы проигнорированы: {' '.join(positional[1:])}", file=sys.stderr)
    if "--strict" in args and not series_dir:
        print("--strict действует только с --series", file=sys.stderr)
    try:
        if series_dir:
            return _run_series(series_dir, args)
        if portfolio_dir:
            return _run_portfolio(portfolio_dir, args)
        return _run_single(positional, args, as_json=as_json, genre=genre,
                           before_path=before_path, want_morph=want_morph, gate=gate)
    except CliError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2


def _run_series(series_dir, args):
    # advisory-гейт: FAIL не валидирует пайплайн без --strict; на коротких
    # сериях возможны ложные срабатывания (см. блок калибровки выше)
    if MORPH is None or RAZDEL is None:
        print("морф-слой недоступен: pip install pymorphy3 razdel", file=sys.stderr)
        return 2
    strict = "--strict" in args
    if "--gate" in args:
        print("--gate в режиме --series не действует", file=sys.stderr)
    d = Path(series_dir)
    if not d.is_dir():
        raise CliError(f"{series_dir}: каталог не найден")
    files = [str(p) for p in sorted(d.glob("*.md")) + sorted(d.glob("*.mdx"))
             if "contaminated" not in p.name]
    # битый UTF-8 не должен валить серию: профиль берём из читаемых файлов
    readable = []
    for f in files:
        try:
            if morph_profile(read_file(f)):
                readable.append(f)
            else:
                print(f"пропущен (нет профиля): {f}", file=sys.stderr)
        except CliError as e:
            print(f"пропущен битый: {e}", file=sys.stderr)
    verdict_s, lines = series_morph_check(readable)
    print(f"серия: {len(files)} файлов ({len(readable)} с профилем),"
          f" advisory-гейт разнообразия: {verdict_s}")
    for l in lines:
        print("  " + l)
    print("  калибровка: ru-bench, 4 эталонные серии против 4 конвейерных серий")
    if verdict_s == "INSUFFICIENT":
        return 2
    if verdict_s == "FAIL":
        print("  FAIL advisory: не блокирует; жёсткий режим через --strict")
        return 1 if strict else 0
    return 0


def _run_portfolio(portfolio_dir, args):
    d = Path(portfolio_dir)
    if not d.is_dir():
        raise CliError(f"{portfolio_dir}: каталог не найден")
    files = [str(p) for p in sorted(d.glob("*.md")) + sorted(d.glob("*.mdx"))
             if "contaminated" not in p.name]
    if not files:
        raise CliError(f"{portfolio_dir}: нет .md файлов")
    texts = []
    for f in files:
        try:
            texts.append((f, read_file(f)))
        except CliError as e:
            print(f"пропущен битый файл: {e}", file=sys.stderr)
    for kind, what, fl in portfolio([f for f, _ in texts]):
        print(f"WARN [серия] {kind}: {what}  ({', '.join(fl)})")
    return 0


def _run_single(positional, args, as_json, genre, before_path, want_morph, gate):
    if "--version" in args:
        print(f"lint.py {LINT_VERSION} (калибровка {CALIB_VERSION})")
        return 0
    if not positional:
        print(__doc__)
        return 2
    path = positional[0]
    text = sys.stdin.read() if path == "-" else read_file(path)
    if not text.strip():
        # пустой stdin (типичный случай - cron-пайплайн) не проходит гейт
        print("ошибка: пустой вход (0 слов) - проверь источник текста", file=sys.stderr)
        return 2

    findings, score, verdict, errors, words, lengths = lint(text, genre=genre)
    fact_report = []
    if before_path:
        fact_report = fact_lock(read_file(before_path), text)
        findings += fact_report
        errors += [f for f in fact_report if f[0] == "ERROR"]
        score = max(0, round(score - 6 * len([f for f in fact_report if f[0] == "ERROR"])))
        verdict = verdict_for(score)

    morph_block = None
    if want_morph:
        prof, zone_rows = morph_check(text)
        to_json = as_json
        if prof is None:
            msg = f"морф-профиль недоступен: {morph_skip_reason(text)}"
            print(msg, file=sys.stderr if to_json else sys.stdout)
        else:
            morph_block = {"profile": prof, "zones": [
                {"metric": m, "value": v, "zone": z, "status": s}
                for m, v, z, s in zone_rows]}
            # в отчёт попадают только отклонения от зон; внутри зоны - тишина
            VERBAL = {
                "part%": ("мало причастий и деепричастий - текст выпрямлен",
                          "много причастий - канцелярит тянет"),
                "advb%": ("мало наречий - текст суховат", "много наречий"),
                "wlen": ("слова короче живых - лексика упрощена",
                         "слова длиннее живых - тяжеловесно"),
                "short8": ("почти нет коротких предложений - ритм выровнен",
                           "много коротких предложений - проверь, не телеграф ли"),
                "ell": ("нет разговорных коротких вставок",
                        "много обрывков <4 слов"),
                "p90p50": ("нет длинных периодов - ритм без длинного хвоста",
                           "очень длинные периоды на фоне коротких"),
            }
            out_text = sys.stderr if to_json else sys.stdout
            for m, v, z, s in zone_rows:
                if s == "ok":
                    continue
                phrase = VERBAL.get(m, ("ниже зоны живых редакций", "выше зоны живых редакций"))[0 if s == "low" else 1]
                print(f"[morph] {m}: {phrase} ({v}, зона {z}) - справка, не вердикт",
                      file=out_text)

    if as_json:
        payload = {
            "linter": {"version": LINT_VERSION,
                       "mode": "morph" if (MORPH is not None and RAZDEL is not None) else "heuristics",
                       "calib": CALIB_VERSION},
            "score": score, "verdict": verdict, "words": words,
            "sentences": len(lengths),
            "findings": [{"severity": s, "cat": c, "rule": r, "line": ln,
                          "excerpt": x[:120], "hint": h}
                         for s, c, r, ln, x, h in findings],
        }
        if morph_block is not None:
            payload["morph"] = morph_block
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        report_text(findings, score, verdict, errors, words, lengths)

    if gate is not None and score < gate:
        return 1
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
