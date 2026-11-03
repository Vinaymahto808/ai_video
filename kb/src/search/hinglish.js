// Query-side Hinglish -> English automotive vocabulary. The website is English, the customer speaks
// Delhi-NCR Hinglish ("brake se awaaz aa rahi hai"); appending the English terms lets both the lexical index
// and the small local embedding model find the right page. Applied to queries only, never to stored text.
export const HINGLISH_TERMS = {
  thanda: "cooling cold", garam: "heating overheat", overheat: "overheat engine cooling",
  awaaz: "noise sound", awaz: "noise sound", jhatka: "jerk vibration", dhuan: "smoke", dhua: "smoke",
  kharab: "faulty not working", "nahi chal": "not working", "start nahi": "not starting battery",
  "chalu nahi": "not starting", gaadi: "car vehicle", gadi: "car vehicle",
  kitna: "price cost", kitne: "price cost", kitni: "price cost", daam: "price", kharcha: "cost", mehnga: "price",
  kab: "when timing", kahan: "where location address", kaha: "where location address", kaise: "how process",
  band: "closed", khula: "open timing", tel: "oil", batti: "light lamp", sheesha: "glass windshield",
  pahiya: "wheel tyre", tyre: "tyre tire wheel", tire: "tyre tire wheel", puncture: "puncture tyre",
  dent: "denting painting body", rang: "paint painting", bima: "insurance claim", claim: "insurance claim",
  uthwa: "pickup", ghar: "home doorstep pickup", pickup: "pickup drop doorstep", "pick up": "pickup drop doorstep",
  warranty: "warranty guarantee", guarantee: "warranty", brake: "brake brakes", clutch: "clutch",
  battery: "battery", suspension: "suspension shocker", shocker: "suspension shocker", ac: "ac air conditioner cooling",
  servicing: "service maintenance", service: "service maintenance", mileage: "mileage fuel",
  accident: "accident collision repair insurance", tutt: "broken", toot: "broken", "nahi ho": "not working",
};

/** "brake se awaaz aa rahi hai" -> same text + " noise sound brake brakes". Cheap, deterministic. */
export function expandQuery(q) {
  const base = String(q || "").toLowerCase();
  const padded = " " + base.replace(/[^\p{L}\p{N}\s]/gu, " ").replace(/\s+/g, " ") + " ";
  const extra = [];
  for (const [k, v] of Object.entries(HINGLISH_TERMS)) {
    if (padded.includes(" " + k + " ") || (k.includes(" ") && padded.includes(k))) extra.push(v);
  }
  return extra.length ? base + " " + [...new Set(extra.join(" ").split(" "))].join(" ") : base;
}
