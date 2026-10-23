LANGUAGES = {
    "English": {"locale": "en-US", "male": "en-US-ChristopherNeural", "female": "en-US-AriaNeural"},
    "Spanish": {"locale": "es-ES", "male": "es-ES-AlvaroNeural", "female": "es-ES-ElviraNeural"},
    "French": {"locale": "fr-FR", "male": "fr-FR-HenriNeural", "female": "fr-FR-DeniseNeural"},
    "German": {"locale": "de-DE", "male": "de-DE-ConradNeural", "female": "de-DE-KatjaNeural"},
    "Italian": {"locale": "it-IT", "male": "it-IT-DiegoNeural", "female": "it-IT-ElsaNeural"},
    "Portuguese (Brazil)": {"locale": "pt-BR", "male": "pt-BR-AntonioNeural", "female": "pt-BR-FranciscaNeural"},
    "Turkish": {"locale": "tr-TR", "male": "tr-TR-AhmetNeural", "female": "tr-TR-EmelNeural"},
    "Arabic": {"locale": "ar-SA", "male": "ar-SA-HamedNeural", "female": "ar-SA-ZariyahNeural"},
    "Hindi": {"locale": "hi-IN", "male": "hi-IN-MadhurNeural", "female": "hi-IN-SwaraNeural"},
    "Russian": {"locale": "ru-RU", "male": "ru-RU-DmitryNeural", "female": "ru-RU-SvetlanaNeural"},
    "Japanese": {"locale": "ja-JP", "male": "ja-JP-KeitaNeural", "female": "ja-JP-NanamiNeural"},
    "Korean": {"locale": "ko-KR", "male": "ko-KR-InJoonNeural", "female": "ko-KR-SunHiNeural"},
    "Chinese (Mandarin)": {"locale": "zh-CN", "male": "zh-CN-YunxiNeural", "female": "zh-CN-XiaoxiaoNeural"},
    "Dutch": {"locale": "nl-NL", "male": "nl-NL-MaartenNeural", "female": "nl-NL-ColetteNeural"},
    "Polish": {"locale": "pl-PL", "male": "pl-PL-MarekNeural", "female": "pl-PL-ZofiaNeural"},
    "Indonesian": {"locale": "id-ID", "male": "id-ID-ArdiNeural", "female": "id-ID-GadisNeural"},
    "Swedish": {"locale": "sv-SE", "male": "sv-SE-MattiasNeural", "female": "sv-SE-SofieNeural"},
    "Ukrainian": {"locale": "uk-UA", "male": "uk-UA-OstapNeural", "female": "uk-UA-PolinaNeural"},
    "Greek": {"locale": "el-GR", "male": "el-GR-NestorasNeural", "female": "el-GR-AthinaNeural"},
    "Hebrew": {"locale": "he-IL", "male": "he-IL-AvriNeural", "female": "he-IL-HilaNeural"},
    "Urdu": {"locale": "ur-PK", "male": "ur-PK-AsadNeural", "female": "ur-PK-UzmaNeural"},
}

LANGUAGE_NAMES = list(LANGUAGES.keys())


def voice_for(language: str, gender: str) -> str:
    entry = LANGUAGES[language]
    return entry["male"] if gender == "Male" else entry["female"]
