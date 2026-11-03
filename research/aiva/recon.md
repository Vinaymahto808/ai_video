# Recon map: AIVA — AI video sales agent widget (web)

Scope: the **AI video chatbot only** — the website widget a visitor talks to, the AI agent's
behaviour during the call, handover to a human, and what one call produces (lead, transcript,
report). Dashboard screens appear only where they configure the AI agent or consume its output.
Out of scope by request: pricing, Reels, live-stream selling, shoppable tiles, co-browsing SDKs,
video emails/QR, Strike-X analytics.
For: The Sachdev Group's own three-brand dealer sites (Galaxy Toyota, Hans Hyundai, Auto Car Repair) — the in-house build in `human_bot/`.
Date: 2026-10-31. Extends `FEATURES.md` (2026-10-29); does not repeat it.

Method: public pages read one at a time, public OpenAPI reference, public Shopify listing. No
account, no private API, no scraping. **Terms of use** (aiva.saleassist.ai/terms-of-use): no clause
on reverse engineering or benchmarking; §1 bars robot/scraper copying, §6 bars "any service based
on our Services" — i.e. resell/wrap theirs. Rebuilding behaviour clean-room is not that. Governing
law India, courts of Haryana.

> Housekeeping: `widget.js` in this folder is a saved copy of their shipped bundle. The clean-room
> rule is not to read or keep bundles; nothing below uses it. Recommend deleting it.

## Sources

| # | source | URL | notes |
| --- | --- | --- | --- |
| 1 | marketing site | https://aiva.saleassist.ai/ | positioning, personas, handover modes, claims |
| 2 | docs index | https://docs.saleassist.ai/llms.txt | 100 pages, all readable as `.md` |
| 3 | widget integration guide | https://docs.saleassist.ai/saleassist-aiva-widget-integration-guide.md | `mountWidget`, `sessionContext` rules |
| 4 | lead-qualification & audit policy | https://docs.saleassist.ai/policies/aiva-meeting-intelligence-lead-qualification-and-audit-rules.md | the post-call JSON contract (already in FEATURES.md §5) |
| 5 | **public OpenAPI spec** | https://platform.saleassist.ai/openapi.json | SaleAssist API 1.0.1 — `SourcesTableModel` is the widget config incl. every AI field; `PeopleTableModel` is the lead |
| 6 | webhook guide | https://docs.saleassist.ai/integration-guide/webhook-integration.md | `widget_new_conversation` payload |
| 7 | inbound call how-to / UX / agent mapping | https://docs.saleassist.ai/features-overview/live-video-calling/inbound-calls/how-to-use.md · …/integration-guide/user-experience.md · …/integration-guide/agent-mapping.md | visitor messages, capture form, routing |
| 8 | desktop & mobile call management | …/inbound-calls/desktop-call-management.md · …/analytics/how-to-manage-calls-on-mobile-application.md | human-side call screen (handover target) |
| 9 | Strike | https://docs.saleassist.ai/features-overview/strike.md | proactive nudge → AI chat/call (kept only as the entry trigger) |
| 10 | AI chat context | https://docs.saleassist.ai/integration-guide/ai-chat-add-context.md | `Allow AI`, `Context Text` with `Context Start`/`Context End` |
| 11 | client onboarding guide | https://docs.saleassist.ai/saleassist.ai-client-onboarding-guide.md | widget inputs, lead dispositions New→In Progress→Closed, go-live checklist |
| 12 | getting started (dashboard map) | https://docs.saleassist.ai/getting-started.md | nav labels, Past Meetings → "Transcription and Summary" |
| 13 | Shopify listing | https://apps.shopify.com/aiva-ai-video-shopping-advisor | screen captions: Detailed Setup, during a call, Leads, Calls, Analytics; "reads each product page automatically" |
| 14 | release logs | https://docs.saleassist.ai/release-logs/v25.01.10.md … v25.02.05-1.md | OpenAI used for summaries/sentiment; Deepgram SDK tried; Exotel telephony; "Take Over" for agents |
| 15 | privacy policy | https://docs.saleassist.ai/policies/privacy-policy.md | what is captured per call; retention is "as long as account exists" |
| 16 | permissions troubleshooting | https://docs.saleassist.ai/troubleshooting/troubleshoot-video-call-permissions.md | cam/mic denied states on iOS/Android |

Not found publicly: walkthrough videos of an AIVA call, the dashboard's AIVA persona editor, the
prompt template, any latency measurement. Searches (web, YouTube) returned only unrelated "AIVA"s.

## Core loop

A visitor on a product page clicks one button and is talking face-to-face with an AI persona
that already knows the page; the AI qualifies them, books or hands over, and the call becomes a
graded lead with a transcript and an audit — without a human being present.

## Screens

Visitor-side (widget) first, then the human-agent and admin screens that the AI loop touches.

| ID | screen | route / how to reach | purpose | key components | states seen |
| --- | --- | --- | --- | --- | --- |
| S01 | Floating launcher | any page with `widget.js` mounted, `form_factor` omitted | invite the visitor in | round/pill launcher (`button_type`, `button_position`), optional welcome video loop, salutation + rotating trigger messages (`trigger_messages`, `trigger_delay/hold/interval`), "Live Now" dot vs offline CTA (`live_cta_text` / `offline_cta_text`) | live, offline, hidden (`is_visible`, `whitelisted_pages`), popup-enlarged (`allow_popup_video`) |
| S02 | Host-site button | `form_factor: "button"` on the site's own CTA | same, from the dealer's own "Talk to Neha" button | none of ours — the site's button | closed, opening |
| S03 | Proactive nudge (Strike) | fires on ≥30 s on page, ≥3 visits, or ≥3 reload/close attempts; exit intent; page/location filters | pull a hesitating visitor in | title, description, image, CTA, Yes/No (`nudge_*`, `strike_yes`/`strike_no`, `exit_intent`) | shown, dismissed, timed out (`strike_timeout`), auto-mode → WhatsApp/AI chat |
| S04 | Channel picker / welcome screen | launcher click | choose video call · audio call · chat · schedule · WhatsApp | welcome video, salutation, channel buttons (`live_conversation_types` vs `offline_conversation_types`), T&C line (`auto_accept_terms`) | live set, offline set, skipped (`skip_welcome_screen`), `chat_first_approach` |
| S05 | Capture form | before the call, or in-call after N s (`in_call_lead_form`, `in_call_lead_form_delay`=2) | collect name / mobile / email / custom fields, routing choice | drag-and-drop form (`lead_form`), OTP step (`capture_lead_otp_params`), business-email-only (`allow_business_email_only`), hidden params from URL/cookies/location (`capture_lead_url_params`, `capture_lead_cookies`, `capture_location`) | empty, validating, OTP pending, `otp_verified`, delayed (`delay_lead_form_video_call`) |
| S06 | Permission prompt | first call attempt | cam/mic access | native browser prompt; `enable_user_camera` decides if camera is asked at all | granted, cancelled (iOS: close + refresh), blocked (Android: reset site permissions) |
| S07 | Connecting / waiting | after accept | mask the join | wait messages (`wait_messages`, `message_interval`), AI-specific hints (`ai_call_waiting_hints`), timeout (`call_timeout`, `customer_waiting_time`) | connecting, queued (`queue_type` fifo/priority), timed out → `timeout_message`, no agent → `agent_unavailable_message` |
| S08 | **AI video call** | AI mode on (`is_enable_ai_video_call_agent_all` or `_offline`) | the product | full-bleed talking-head persona (`avatar_vendor`/`avatar_id`, `tavus_replica_id`/`tavus_persona_id`), portrait option (`enable_portrait_mode_aiva`), background (`ai_chat_background`), opening line (`ai_agent_greeting_message`), live captions/chat strip, bottom bar: mic, camera, chat, end; self-view PIP; "talk to a human" | greeting, listening, speaking, thinking, tool-in-progress (screenshare `is_ai_screenshare_tool`, schedule `is_ai_schedule_call`), failure (`ai_agent_failure_message`), handover (`is_ai_transfer_to_agent`) |
| S09 | AI audio call | `is_enable_ai_audio_call_agent_*` | same loop, no face | persona still + waveform, same bar | same as S08 |
| S10 | AI chat panel | `allow_ai_chat`, channel "AI Chat" | text fallback / chat-first | bot name + icon (`ai_chat_bot_name`, `ai_chat_bot_icon`), welcome (`ai_chat_welcome_message`), bubbles, handover line (`ai_chat_handover_message`, `ai_chat_handover_failed_message`) | bot, waiting for agent approval, human, failed |
| S11 | Hybrid: AI + human in one room | `is_enable_ai_video_call_agent_with_real_agent`, `aiva_backup` | human leads, AI steps in / AI leads, human joins | two tiles; "Take Over" on agent side | AI-only, both, human-only |
| S12 | Schedule a call | channel "Schedule", or AI books it (`is_ai_schedule_call`) | book a slot | form → slot grid (`get_available_slots`, `schedule_meet_working_hours_day_wise`) → email(s) → Confirm slot → Invite friends | no slots, confirmed (email + WhatsApp to both sides) |
| S13 | Post-call thank-you | call ends | close the loop | thank-you message, optional feedback (`capture_feedback`: text/form/audio/video) | with/without feedback |
| S14 | Missed-call message | nobody (AI off, agents busy) | fallback | `missed_call_message`, leave-a-message / WhatsApp (`allow_whatsapp_message`, `whatsapp_number`) | — |
| A01 | Agent incoming-call alert | desktop notification / mobile push | human handover target | "attend call" | ringing, picked, missed (`widget_request_missed`) |
| A02 | Agent in-call screen | after attend | run or take over the call | cam/mic/flip, chat, screen share, end; Active Request (customer details); in-call notes; ⋮ record/hold/PIP/virtual background; Transfer; Share link; Take Over (from AI); feedback form icon | live, on hold, transferred |
| A03 | Lead record | Leads → lead | everything the AI produced | contact, `lead_status`/`lead_type` chips, `ai_lead_status`, conversations timeline, recording, **Transcription and Summary** (summary, sentiment, keywords, questions, action items), WhatsApp tab, Add Task, Sale Converted | new, in-progress, follow-up needed, closed |
| A04 | Widget → AI Bot settings | Assets → Widgets → widget → AI Bot | configure the agent | Allow AI, Chat Bot Name, Context Text (`Context Start … Context End`), per-field config above; vendor keys (`avatar_api_key`, `tts_vendor`, `tts_voice_id`, `asr_vendor`, `asr_language`), `aiva_mode`, `enable_tools`, FAQ list (`ai_faq_context_json`), structured context (`ai_context_json`, `ai_contexts`, `ai_contexts_video`), `screenshare_context` | — |
| A05 | Widget → User Experience | same widget | all visitor copy | welcome, trigger, wait, timeout, missed, thank-you messages; capture form builder | — |
| A06 | Widget → Channels & routing | same widget | live/offline channel mix, agents, routing | Live agent interaction / Offline agent interaction dropdowns, Auto Record, queue type, default agents, form-/location-/time-based routing, availability | — |
| A07 | Past Meetings | Meetings → Past | review calls | table: customer, contact, time, widget, attendees, duration; eye, recording, Transcription and Summary | — |
| A08 | Analytics (AIVA) | Shopify listing: "Analytics view in AIVA" | calls, leads, carts attributed | counts + funnel (public detail thin) | — |

## Flows

```
F01 Visitor talks to the AI video agent (the core loop)
    S01 launcher -> S04 channel picker -> [S05 capture form] -> S06 permissions -> S07 connecting -> S08 AI call -> S13 thank-you
    happy path clicks: 3 (launcher, "Video call", allow) — 4 with the capture form; Shopify copy promises "one click", i.e. S04/S05 skipped (`skip_welcome_screen`, in-call form)
    AI opens with `ai_agent_greeting_message`, already holding sessionContext (page/product/price/stock) + ai_contexts + FAQ
    edge: cam/mic blocked (S06 states) · AI fails to connect -> `ai_agent_failure_message` · visitor goes silent · visitor interrupts mid-sentence · asks for a human (F03) · asks for a price not in context · mobile portrait

F02 Proactive entry
    S03 nudge "Yes" -> (manual) S07 -> S08   | (auto) WhatsApp AI chat -> optional human
    edge: dismissed thrice · exit intent on checkout · frequency: `nudge_loop`, `trigger_loop`

F03 AI -> human handover ("AIVA first")
    S08 ("talk to someone" or AI decides via `is_ai_transfer_to_agent`) -> S07 queue -> A01 ring -> A02 agent joins same room -> S11 -> human ends -> S13
    events: widget_bot_handover, conversation_handover_status_changes; lead.lead_handover_status = true
    edge: no agent online -> AI keeps the call, offers S12 schedule or WhatsApp (`aiva_backup`) · agent declines · hours (`working_hours_day_wise`)

F04 Human first -> AI backup
    S01 (live) -> S07 ring humans -> timeout -> S08 AI takes the call (`is_enable_ai_video_call_agent_offline` = AI only when humans are offline)

F05 AI books a test drive / consultation
    S08 -> AI calls schedule tool (`is_ai_schedule_call`) -> slot from get_available_slots -> ScheduleMeeting{people_id,date,time,timezone,form_id,invite_friends} -> confirmation email + WhatsApp -> A03 lead shows scheduled meeting
    edge: no slot that day · time zone · visitor gives no email

F06 Lead capture during the call
    S05 in-call form after `in_call_lead_form_delay` s -> OTP -> `/{identifier}/capture` creates/updates People -> webhook widget_form_captured / widget_lead_added -> CRM
    edge: visitor refuses -> lead still exists as anonymous People row (unique_field_value), identified later

F07 Post-call intelligence
    meeting_ended -> recording -> transcript (TranscriptResponseModel.text) -> LLM report per policy (summary, sentiment[+segments], keywords, questions, action_items, lead_status HOT/WARM/COLD, miscellaneous weighted Qs, unanswered_questions, audit_details) -> A03 lead (`ai_lead_status`) + A07 -> webhooks widget_lead_updated
    note: release log v25.01.10 names OpenAI for summaries/sentiment and "keywords, FAQs, action items" on transcripts

F08 Admin configures the agent
    A04 AI Bot (Allow AI, name, Context Text, FAQ, vendors, voice, avatar) -> A05 copy -> A06 channels/agents/hours -> integration snippet (`mountWidget` + sessionContext) -> test call
```

## Components

| component | variants | states | used on |
| --- | --- | --- | --- |
| Launcher | floating round, pill with text, host button, popup-video enlarged | live, offline, hidden | S01 S02 |
| Trigger/salutation bubble | text, rotating list | shown, cycling, dismissed | S01 S03 |
| Nudge card | image + title + description + CTA, Yes/No | shown, timed out | S03 |
| Channel button | video, audio, chat, schedule, WhatsApp, leave message | enabled, offline-only | S04 S14 |
| Capture form | pre-call, in-call delayed, OTP step | empty, invalid, verifying, verified | S05 |
| Persona stage | video talking head, portrait mode, still + waveform (audio) | idle/listening, speaking, thinking, tool running, error | S08 S09 |
| Live caption / transcript strip | partial ASR, final | streaming, final | S08 S09 S10 |
| Call bar | mic, camera, flip, chat, screen share (agent), end, "talk to human" | on/off, disabled | S08 S09 S11 A02 |
| Chat panel | bot, human, system line | typing, waiting approval, failed | S10 |
| Waiting message | wait text, AI hint, timeout | cycling | S07 |
| Slot picker | day strip + time grid | no slots, selected, confirmed | S12 |
| Lead chips | lead_status (coloured, custom names), lead_type, ai_lead_status HOT/WARM/COLD | — | A03 |
| Transcript & summary card | summary, sentiment bar + segments, keywords, questions, action items, audit score | loading, ready, unavailable | A03 A07 |
| Feedback form | agent checklist (custom), visitor text/audio/video | — | A02 S13 |
| Toast / notification | incoming call, new lead, missed call | — | A01 |

## Inferred data model

Field names below are from the public OpenAPI spec unless marked [I] inferred.

```
Source (= one widget)   id, client_id, source_type (audio_call|talk_to_me|live_call),
                        source_intent (general|home_page|product_page|cart_page|checkout_page|contact_page|blog),
                        is_enabled, is_visible, whitelisted_pages, button_type, button_position,
                        live_conversation_types[], offline_conversation_types[],
                        -- AI --
                        allow_ai, allow_ai_chat, aiva_mode, aiva_backup, enable_tools,
                        is_enable_ai_video_call_agent_all/_offline, is_enable_ai_audio_call_agent_all/_offline,
                        is_enable_ai_video_call_agent_with_real_agent, is_conversational_avatar_enabled,
                        is_agora_conversational_ai, is_ai_schedule_call, is_ai_screenshare_tool,
                        is_ai_transfer_to_agent, allow_ai_agent_in_scheduled_call, enable_portrait_mode_aiva,
                        ai_agent_greeting_message, ai_agent_failure_message, ai_call_waiting_hints[],
                        ai_chat_bot_name, ai_chat_bot_icon, ai_chat_welcome_message, ai_chat_handover_message,
                        ai_chat_handover_failed_message, ai_chat_background,
                        ai_contexts{}, ai_contexts_video{}, ai_context_json[{}], ai_faq_context_json[], screenshare_context[{}],
                        avatar_vendor, avatar_api_key, avatar_id, tavus_replica_id, tavus_persona_id,
                        tts_vendor, tts_voice_id, asr_vendor, asr_language, region_avatars{},
                        -- visitor copy --
                        welcome_message, welcome_salutation, trigger_messages[], wait_messages[],
                        timeout_message, missed_call_message, agent_unavailable_message, live_cta_text, offline_cta_text,
                        -- capture --
                        capture_lead_form, lead_form, in_call_lead_form, in_call_lead_form_delay,
                        capture_lead_otp_params, lead_otp_params, capture_lead_url_params, capture_lead_cookies,
                        capture_location, allow_business_email_only, unique_field_name,
                        capture_feedback, feedback_form, capture_agent_feedback, agent_feedback_form,
                        -- routing / hours --
                        agents, scheduling_agents, form_based_routing{}, location_based_routing{}, time_based_routing{},
                        queue_type (fifo|priority), call_timeout, customer_waiting_time,
                        working_hours_day_wise, schedule_meet_working_hours_day_wise, auto_record
                        evidence: SourcesTableModel (spec), A04–A06, docs 3/7/10
                        confidence: high (names), medium (semantics of aiva_mode / ai_contexts shape)

People (= lead)         id, client_id, unique_field_value, name, email, mobile_number, tags[],
                        lead_status (prospect|raw|assigned|working|qualified|nurture|unqualified; default prospect),
                        lead_status_name/_color, lead_type_name/_color (tenant-defined),
                        ai_lead_status (HOT|WARM|COLD [I]), lead_handover_status, current_workflow_node{},
                        lead_source (default "direct"), assigned_to, lead_owned_by, last_attended_by,
                        last_source_id, last_form_id, last_conversation_id, last_conversation_time,
                        last_cta_clicked, is_connected, last_seen_on, followup_requested,
                        is_lead_purchased, lead_purchased_comment, is_lead_blocked, is_lead_archived,
                        unread_conversations, is_conversation_read, created_on, updated_on
                        evidence: PeopleTableModel, webhook payload.people_detail (adds address, pincode, utm_campaign,
                        originated_from, product_category, product_sku, device, form_fill, otp_verified,
                        widget_city/state/country/lat/long/ip, extra1, extra2)
                        confidence: high

Conversation            message_content, message_type, message_from, people_id, source_id, client_id
                        evidence: ConversationsTableModel        confidence: high

CallRequest             conversation_id, source_id, source_type, people_id, message_type, is_connected, is_transferred
                        evidence: CallRouterTableModel; agent endpoints has_incoming_requests / attend / end
                        confidence: high

Meeting                 id, meeting_type (live_call|…), moderators[], participants[], date, time, timezone,
                        duration, meeting_status (incl. missed), recordings[], streamings[], client_id,
                        attend_schedule_call_before, invite_friends[]
                        evidence: MeetingsTableModel, ScheduleMeeting, release logs   confidence: high

Transcript              id, meeting_id, url, size, duration, is_available, text, summary
                        + report (not a public schema; from policy doc): sentiment, sentiment_score, keywords[],
                        sentiment_segments[], questions[], action_items[], lead_status, miscellaneous[{question,answer,weight,score}],
                        unanswered_questions[], audit_details
                        evidence: TranscriptResponseModel, policy doc, A03/A07 UI   confidence: high / medium (where stored)

Form                    data{}, people_id, source_id            evidence: FormsTableModel   confidence: high

Agent (user)            status (away|offline|online|busy), role (agent|admin), widgets[], working hours, leaves
                        evidence: AgentStatusEnum, docs 7, availability endpoints   confidence: high

Webhook                 name, event (WebhooksEventsEnum), url, is_enabled, headers{}, cookies{}, mapping_field{}
                        events relevant to the AI loop: widget_incoming_request(_notify/_picked/_closed),
                        widget_no_agent_available, widget_form_captured, widget_new_conversation, widget_lead_added,
                        widget_lead_updated, widget_request_missed, widget_bot_handover,
                        conversation_handover_status_changes, meeting_created/started/user_joined/user_left/ended,
                        meeting_recording_started/ended/available
                        confidence: high

SessionContext [I]      arbitrary JSON from the host page, merged, keys read by the prompt
                        evidence: integration guide          confidence: high (contract), unknown (storage)
```

Relationships: Client 1-n Source · Source 1-n CallRequest · People 1-n Conversation · People 1-n Form ·
People 1-n Meeting · Meeting 1-1 Transcript · Source n-n Agent · Client 1-n Webhook.

What the field names tell you about the stack (public spec, not the bundle): the avatar layer is
pluggable with a first-class **Tavus** slot (`tavus_replica_id`, `tavus_persona_id`) plus generic
`avatar_vendor/avatar_id`; there is an **Agora Conversational AI** mode (`is_agora_conversational_ai`)
beside the Agora RTC transport already noted in FEATURES.md; ASR and TTS are vendor slots per widget
(`asr_vendor`, `tts_vendor`, `tts_voice_id`), with Deepgram attempted in release notes; post-call
reports via OpenAI. The AI has three tools (`enable_tools`): schedule a call, screen-share content,
transfer to agent. Knowledge is prompt context — free text, FAQ list, structured JSON per page —
not a trained model.

## Feature matrix

See `features.csv`. Must: 29, should: 20, could: 7, skip: 4 (60 rows; today: yes 10, partial 11, no 35). The `clone` column is filled against
`human_bot/` as it stands today (yes / partial / no).

## Out of scope (cannot or should not be cloned)

- Their persona faces, voices, welcome videos, prompt text, UI copy — all theirs; ship your own
  (the consented Neha voice and portrait in `voice_clone/`, `tran_moment/`).
- Tavus/Agora/OpenAI are vendor choices, not features; pick your own stack.
- "1M+ conversations" and tenant data — the network effect of their audit loop is theirs; yours starts at zero and grows from your own calls.
- Shopify app, Wix plugin, mobile SDKs, co-browse, Reels, live stream, video email/QR, Strike-X de-anonymisation — excluded by request.
- Pricing — excluded by request.

## Size

Screens 14 visitor + 8 agent/admin, flows 8, entities 9. Hard parts: (1) full-duplex real-time
transport with sub-second turn-taking (WebRTC + streaming ASR/LLM/TTS), (2) talking-head avatar
driven live from TTS audio with idle motion, (3) AI-to-human handover inside the same media room
with state carried over, (4) tool calls mid-conversation (slot booking, transfer) without breaking
speech, (5) the post-call report pipeline at the policy's exact contract. Size: **L** (a quarter)
for parity on the core loop; `human_bot/` already covers the brain, humanizer, persona config and
the post-call JSON, so the remaining work is transport, voice, face, handover and the lead/admin
surface. No promise of a perfect clone.

Next: `/replica-architect` to turn the Source/People/Meeting/Transcript model into a schema and
pick the WebRTC + ASR/TTS stack.
