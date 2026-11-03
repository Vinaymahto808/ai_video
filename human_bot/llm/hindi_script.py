"""Roman-script Hinglish -> Devanagari for the Hindi words only.

Why: an English neural voice reading "aap kis area mein hain" applies English phonetics to the Hindi words
("mein" -> "mine", "hai" -> "hay"), which is what makes the bot sound robotic. A hi-IN voice pronounces
Devanagari natively and reads the English words that stay in Latin script with an Indian-English accent,
which is exactly the Delhi-NCR Hinglish we want.

Only words in the dictionary are converted; anything else (service, pickup, Gurgaon, AC, Swift) is left
alone, so an unknown Hindi word is never mangled worse than it is today. Punctuation and case are kept.
"""
from __future__ import annotations

import re

# Roman spelling (lower-case) -> Devanagari. Several spellings map to one word on purpose.
WORDS: dict[str, str] = {
    # greetings / particles
    "namaste": "नमस्ते", "namaskar": "नमस्कार", "ji": "जी", "haan": "हाँ", "ha": "हाँ", "han": "हाँ", "nahi": "नहीं",
    "nahin": "नहीं", "nai": "नहीं", "na": "ना", "bhi": "भी", "toh": "तो", "to": "तो", "hi": "ही", "aur": "और", "ya": "या",
    "lekin": "लेकिन", "par": "पर", "pe": "पे", "kyunki": "क्योंकि", "kyuki": "क्योंकि", "agar": "अगर", "phir": "फिर",
    "fir": "फिर", "bas": "बस", "sirf": "सिर्फ़", "matlab": "मतलब", "shukriya": "शुक्रिया", "dhanyavaad": "धन्यवाद",
    "maaf": "माफ़", "arre": "अरे", "achha": "अच्छा", "acha": "अच्छा", "accha": "अच्छा", "achi": "अच्छी", "achhi": "अच्छी",
    "theek": "ठीक", "thik": "ठीक", "sahi": "सही", "galat": "ग़लत", "bilkul": "बिल्कुल", "zaroor": "ज़रूर", "jarur": "ज़रूर",
    "haanji": "हाँ जी", "okay": "okay",
    # postpositions / pronouns
    "ka": "का", "ki": "की", "ke": "के", "ko": "को", "se": "से", "mein": "में", "me": "में", "main": "मैं", "mujhe": "मुझे",
    "mera": "मेरा", "meri": "मेरी", "mere": "मेरे", "hum": "हम", "humein": "हमें", "hume": "हमें", "hamara": "हमारा",
    "hamari": "हमारी", "hamare": "हमारे", "humara": "हमारा", "humari": "हमारी", "humare": "हमारे", "aap": "आप",
    "aapka": "आपका", "aapki": "आपकी", "aapke": "आपके", "aapko": "आपको", "aapse": "आपसे", "apna": "अपना", "apni": "अपनी",
    "apne": "अपने", "unka": "उनका", "unki": "उनकी", "unke": "उनके", "unko": "उनको", "uska": "उसका", "uski": "उसकी",
    "uske": "उसके", "usko": "उसको", "iska": "इसका", "iski": "इसकी", "iske": "इसके", "isko": "इसको", "yeh": "यह", "ye": "ये",
    "woh": "वो", "wo": "वो", "is": "इस", "us": "उस", "isme": "इसमें", "usme": "उसमें", "inme": "इनमें", "yahan": "यहाँ",
    "yaha": "यहाँ", "wahan": "वहाँ", "waha": "वहाँ", "sab": "सब", "sabhi": "सभी", "kuch": "कुछ", "kuchh": "कुछ", "koi": "कोई",
    "kisi": "किसी", "kis": "किस", "kaun": "कौन", "kaunsa": "कौनसा", "kaunsi": "कौनसी", "kaunse": "कौनसे", "konsa": "कौनसा",
    "konsi": "कौनसी", "kya": "क्या", "kyun": "क्यों", "kyu": "क्यों", "kab": "कब", "kahan": "कहाँ", "kaha": "कहाँ",
    "kaise": "कैसे", "kaisa": "कैसा", "kaisi": "कैसी", "kitna": "कितना", "kitni": "कितनी", "kitne": "कितने",
    "jo": "जो", "jab": "जब", "tab": "तब", "jahan": "जहाँ", "jaise": "जैसे", "waise": "वैसे", "aise": "ऐसे", "aisa": "ऐसा",
    "aisi": "ऐसी", "wala": "वाला", "wali": "वाली", "wale": "वाले", "liye": "लिए", "saath": "साथ", "sath": "साथ",
    "bina": "बिना", "baad": "बाद", "pehle": "पहले", "pahle": "पहले", "andar": "अंदर", "bahar": "बाहर", "upar": "ऊपर",
    "neeche": "नीचे", "paas": "पास", "taraf": "तरफ़", "tak": "तक", "hisaab": "हिसाब", "hisab": "हिसाब", "alawa": "अलावा",
    # verbs: hona / karna / forms
    "hai": "है", "hain": "हैं", "hoon": "हूँ", "hu": "हूँ", "hun": "हूँ", "ho": "हो", "tha": "था", "thi": "थी", "the": "थे",
    "hoga": "होगा", "hogi": "होगी", "honge": "होंगे", "hona": "होना", "hone": "होने", "hota": "होता", "hoti": "होती",
    "hote": "होते", "hua": "हुआ", "hui": "हुई", "hue": "हुए", "jayega": "जाएगा", "jayegi": "जाएगी", "jayenge": "जाएंगे",
    "jaega": "जाएगा", "jaegi": "जाएगी", "jaana": "जाना", "jaate": "जाते", "jaata": "जाता", "jaati": "जाती", "gaya": "गया",
    "gayi": "गई", "gaye": "गए", "gai": "गई", "aana": "आना", "aata": "आता", "aati": "आती", "aate": "आते", "aaye": "आए",
    "aayi": "आई", "aaya": "आया", "aaiye": "आइए", "karna": "करना", "karne": "करने", "karni": "करनी", "karta": "करता",
    "karti": "करती", "karte": "करते", "karo": "करो", "kar": "कर", "karo": "करो", "kijiye": "कीजिए", "kijiye": "कीजिए",
    "kariye": "करिए", "karein": "करें", "kare": "करे", "karenge": "करेंगे", "karegi": "करेगी", "karega": "करेगा",
    "karke": "करके", "kiya": "किया", "kiye": "किए", "karwa": "करवा", "karwana": "करवाना", "karwani": "करवानी",
    "karwaiye": "करवाइए", "karwa": "करवा", "karwate": "करवाते", "karwa": "करवा", "karwaye": "करवाएं",
    "dena": "देना", "deti": "देती", "deta": "देता", "dete": "देते", "dijiye": "दीजिए", "do": "दो", "de": "दे",
    "dunga": "दूँगा", "dungi": "दूँगी", "denge": "देंगे", "diya": "दिया", "lena": "लेना", "leti": "लेती", "leta": "लेता",
    "lete": "लेते", "lijiye": "लीजिए", "lo": "लो", "le": "ले", "liya": "लिया", "lein": "लें", "lenge": "लेंगे",
    "bata": "बता", "batana": "बताना", "batati": "बताती", "batata": "बताता", "bataiye": "बताइए", "batao": "बताओ",
    "bataungi": "बताऊँगी", "batayenge": "बताएंगे", "bataye": "बताएं", "batayein": "बताएं", "batayega": "बताएगा",
    "batayegi": "बताएगी", "bol": "बोल", "boliye": "बोलिए", "bolo": "बोलो", "bolna": "बोलना", "bolte": "बोलते",
    "bolti": "बोलती", "sun": "सुन", "suniye": "सुनिए", "sunti": "सुनती", "sunna": "सुनना", "sunte": "सुनते",
    "dekh": "देख", "dekhiye": "देखिए", "dekho": "देखो", "dekhte": "देखते", "dekhti": "देखती", "dekhna": "देखना",
    "dekhenge": "देखेंगे", "dekh": "देख", "samajh": "समझ", "samjha": "समझा", "samjhi": "समझी", "samajhna": "समझना",
    "chahiye": "चाहिए", "chahte": "चाहते", "chahti": "चाहती", "chahta": "चाहता", "chahenge": "चाहेंगे",
    "chahengi": "चाहेंगी", "sakte": "सकते", "sakti": "सकती", "sakta": "सकता", "sakenge": "सकेंगे", "paayi": "पाई",
    "paya": "पाया", "paa": "पा", "paate": "पाते", "raha": "रहा", "rahi": "रही", "rahe": "रहे", "rahega": "रहेगा",
    "rahegi": "रहेगी", "rakhiye": "रखिए", "rakh": "रख", "rakhte": "रखते", "lagega": "लगेगा", "lagegi": "लगेगी",
    "lagta": "लगता", "lagti": "लगती", "lagte": "लगते", "laga": "लगा", "lagi": "लगी", "milega": "मिलेगा",
    "milegi": "मिलेगी", "milta": "मिलता", "milti": "मिलती", "milte": "मिलते", "mil": "मिल", "mila": "मिला",
    "chalta": "चलता", "chalti": "चलती", "chalte": "चलते", "chalate": "चलाते", "chalega": "चलेगा", "chalegi": "चलेगी",
    "chal": "चल", "chaliye": "चलिए", "bhej": "भेज", "bhejti": "भेजती", "bhejta": "भेजता", "bhejna": "भेजना",
    "bhej": "भेज", "bhijwa": "भिजवा", "bhejenge": "भेजेंगे", "uthwa": "उठवा", "uthwani": "उठवानी", "aa": "आ",
    "aaunga": "आऊँगा", "aayegi": "आएगी", "aayega": "आएगा", "aayenge": "आएंगे", "jaa": "जा", "jaiye": "जाइए",
    "soch": "सोच", "sochte": "सोचते", "sochiye": "सोचिए", "pooch": "पूछ", "poochiye": "पूछिए", "poochna": "पूछना",
    "pucho": "पूछो", "puchiye": "पूछिए", "banwa": "बनवा", "banana": "बनाना", "ban": "बन", "banega": "बनेगा",
    "banegi": "बनेगी", "khul": "खुल", "khula": "खुला", "khuli": "खुली", "khulta": "खुलता", "band": "बंद",
    "ruk": "रुक", "rukiye": "रुकिए", "thoda": "थोड़ा", "thodi": "थोड़ी", "thode": "थोड़े", "zyada": "ज़्यादा",
    "jyada": "ज़्यादा", "kam": "कम", "bahut": "बहुत", "bohot": "बहुत", "bahot": "बहुत", "poora": "पूरा", "poori": "पूरी",
    "pura": "पूरा", "puri": "पूरी", "ek": "एक", "do": "दो", "teen": "तीन", "chaar": "चार", "paanch": "पाँच",
    "das": "दस", "lakh": "लाख", "hazaar": "हज़ार", "hazar": "हज़ार", "rupaye": "रुपये", "rupay": "रुपये", "paise": "पैसे",
    # nouns / adjectives common in a workshop / showroom call
    "gaadi": "गाड़ी", "gadi": "गाड़ी", "gaadiyan": "गाड़ियाँ", "kaam": "काम", "baat": "बात", "baatein": "बातें",
    "din": "दिन", "raat": "रात", "subah": "सुबह", "shaam": "शाम", "aaj": "आज", "kal": "कल", "parso": "परसों",
    "abhi": "अभी", "turant": "तुरंत", "jaldi": "जल्दी", "der": "देर", "waqt": "वक़्त", "time": "time", "ghar": "घर",
    "jagah": "जगह", "pata": "पता", "number": "number", "naam": "नाम", "dikkat": "दिक्कत", "pareshani": "परेशानी",
    "awaaz": "आवाज़", "awaz": "आवाज़", "thanda": "ठंडा", "thandi": "ठंडी", "garam": "गरम", "dhuan": "धुआँ",
    "jhatka": "झटका", "kharab": "ख़राब", "naya": "नया", "nayi": "नई", "purana": "पुराना", "purani": "पुरानी",
    "chota": "छोटा", "choti": "छोटी", "bada": "बड़ा", "badi": "बड़ी", "sasta": "सस्ता", "sasti": "सस्ती",
    "mehnga": "महँगा", "mehngi": "महँगी", "mahanga": "महँगा", "sahi": "सही", "asli": "असली", "nakli": "नकली",
    "tez": "तेज़", "dheere": "धीरे", "aaram": "आराम", "aasaan": "आसान", "mushkil": "मुश्किल", "zaroori": "ज़रूरी",
    "jaruri": "ज़रूरी", "khaas": "ख़ास", "alag": "अलग", "wapas": "वापस", "wapis": "वापस", "saamne": "सामने",
    "insaan": "इंसान", "aadmi": "आदमी", "bhai": "भाई", "sir": "sir", "madam": "madam", "team": "team",
    "haftе": "हफ़्ते", "hafte": "हफ़्ते", "hafta": "हफ़्ता", "mahina": "महीना", "mahine": "महीने", "saal": "साल",
    "baar": "बार", "dobara": "दोबारा", "phirse": "फिर से", "ekdum": "एकदम", "kaafi": "काफ़ी", "kafi": "काफ़ी",
    "shayad": "शायद", "pakka": "पक्का", "pakki": "पक्की", "sach": "सच", "jhoot": "झूठ", "umeed": "उम्मीद",
    "madad": "मदद", "sawaal": "सवाल", "sawal": "सवाल", "jawaab": "जवाब", "jawab": "जवाब", "kharcha": "ख़र्चा",
    "daam": "दाम", "keemat": "क़ीमत", "badhiya": "बढ़िया", "mast": "मस्त", "theekhai": "ठीक है",
    "gurgaon": "Gurgaon", "dilli": "दिल्ली", "noida": "Noida",
    # names the persona says about herself (an English voice reads "Neha" as "Nee-ha")
    "neha": "नेहा", "riya": "रिया", "priya": "प्रिया",
}

_WORD = re.compile(r"[A-Za-z]+")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def to_devanagari(text: str) -> str:
    """Convert the dictionary's Hindi words to Devanagari; everything else is returned untouched."""
    if not text or _DEVANAGARI.search(text):
        return text  # already Hindi script (customer typed Hindi): leave it

    def repl(m: re.Match) -> str:
        w = m.group(0)
        if w.isupper() and len(w) <= 4:  # AC, EMI, SUV, GST: acronyms stay English
            return w
        return WORDS.get(w.lower(), w)

    return _WORD.sub(repl, text)


def hindi_ratio(text: str) -> float:
    """Share of alphabetic words that the dictionary knows: a rough 'how Hinglish is this sentence'."""
    words = _WORD.findall(text)
    if not words:
        return 0.0
    return sum(1 for w in words if w.lower() in WORDS) / len(words)
