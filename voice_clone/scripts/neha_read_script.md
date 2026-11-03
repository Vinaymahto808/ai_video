# Neha voice: recording script for the voice artist

Purpose: a dry, conversational recording of the artist who voices the ACR ads, so the website assistant
("Neha") can use her timbre. The four ad masters in `human_bot/web/samples/` are style references only:
they have a music bed and limiting and cannot be used for cloning.

## Before recording (owner / studio)

- **Consent.** The artist signs the consent form (`voice_clone/consent/`), naming this use: a synthetic
  voice for the Auto Car Repair website and WhatsApp assistant, Hindi/Hinglish/English, revocable.
  No recording is processed without it (DPDP Act 2023; project rule).
- **Setup.** Quiet room (no AC hiss, no traffic), USB mic or phone 20 cm from the mouth, pop filter or a
  sock over the mic. WAV, mono, 44.1 or 48 kHz, 24-bit if possible. Peaks around -6 dB, never clipping.
  **No music, no reverb, no compression, no noise reduction.** One file per section is fine.
- **Delivery.** Talking to one customer on a call, not reading an ad. Warm, quick, Delhi-NCR Hinglish,
  natural pauses, a smile in the voice. Mistakes: pause two seconds and say the line again.
- **Length.** Sections 1 to 6 are about 3 minutes (enough for a timbre match). All sections are about
  12 to 15 minutes (enough for the RVC conversion model). More natural talk is always better than more lines.

## 1. Greetings and openers

1. Namaste! Main Neha, Auto Car Repair se. Aapki gaadi mein kya problem hai?
2. Namaste, wapas swagat hai! Pichli baar aapki Creta ki AC wali baat hui thi, kya woh theek ho gayi?
3. Haan ji, boliye. Main sun rahi hoon.
4. Good morning! Auto Car Repair se Neha baat kar rahi hoon.
5. Hello ji, kaise hain aap? Bataiye, kya help kar sakti hoon?
6. Yeh baat-cheet quality ke liye record hoti hai.

## 2. Backchannels and fillers (say each one three times, differently)

acha · haan ji · hmm · right · samajh gayi · theek hai ji · ok ji · bilkul · sahi hai · ek second

## 3. Questions back to the customer

1. Aapki gaadi kaunsi hai, make aur model?
2. Gaadi mein kya problem hai, ya kaunsi service karwani hai?
3. Aap kis area mein hain, Gurgaon, Delhi ya Noida?
4. Kab convenient rahega, aaj ya kal?
5. Kya aap mujhe apna number bata denge, taaki team call kar sake?
6. Brake se awaaz kab se aa rahi hai, chalate time ya brake dabate time?
7. AC thanda bilkul nahi kar raha, ya kam kar raha hai?
8. Gaadi warranty mein hai abhi?
9. Aap pickup chahte hain ya workshop aana prefer karenge?
10. Insurance claim ke liye accident kab hua tha?

## 4. Answers and reassurance

1. Koi dikkat nahi, yeh aam problem hai, AC service se theek ho jayegi.
2. Haan ji, Gurgaon mein free pickup aur drop hai.
3. Exact quote gaadi aur kaam par depend karta hai. Main team se quote dilwa deti hoon.
4. Genuine parts ke saath kaam hota hai, aur warranty bhi milti hai.
5. Insurance ka paperwork, surveyor coordination, sab hum handle karte hain. Aap tension mat lijiye.
6. Perfect. Swift ki periodic service, Noida se kal subah pickup. Team call karke time confirm karegi.
7. Done ji. Thodi der mein aapko call aa jayega.
8. Maaf kijiye, main theek se samajh nahi paayi. Dobara boliye please.
9. Ek second, line thodi kharab hai. Dobara boliye.
10. Main aapko abhi Auto Car Repair ki team se connect kar deti hoon.

## 5. Numbers, names, places (read naturally, not digit by digit unless marked)

1. Aapka number nau-aath-ek-ek, do-teen-chaar-paanch, chhe-saat hai, sahi hai na? (digits)
2. Service ka time lagbhag do se teen ghante lagta hai.
3. Pickup subah dus baje ya dopahar do baje, kaunsa theek rahega?
4. Hamare workshops Gurgaon Sector 14, Delhi Moti Nagar aur Noida Sector 63 mein hain.
5. Creta, Swift, Baleno, Nexon, Innova, Fortuner, City, Verna, Seltos, XUV700.
6. Maruti, Hyundai, Honda, Toyota, Tata, Mahindra, Kia, MG, Skoda, Volkswagen.
7. Up to tees percent tak saving ho sakti hai OEM ke comparison mein.
8. Ek baje, dedh baje, do baje, dhai baje, teen baje.

## 6. Closings

1. Thank you ji. Details main WhatsApp par bhej deti hoon.
2. Aapka time diya, shukriya. Koi aur help chahiye toh bataiyega.
3. Theek hai ji, phir milte hain. Gaadi ka dhyaan rakhiye!
4. Bye ji, have a good day.

## 7. Free talk (for the longer model; 8 to 10 minutes)

Talk naturally, as on a real call, about:
- how a periodic service happens at the workshop, step by step;
- what to do right after a small accident, and how the insurance claim goes;
- why AC stops cooling in Delhi summers and what the fix usually is;
- a story about a difficult customer who ended up happy;
- directions to the Gurgaon workshop from the highway.

Mix Hindi and English the way you speak; include "umm", short laughs, and restarts, they help.

## After recording

Send the WAV files and the signed consent. Processing happens on Colab (`voice_clone/*.ipynb`), never on
this laptop; the resulting model is stored under `voice_models/clone/` with the consent record beside it.
