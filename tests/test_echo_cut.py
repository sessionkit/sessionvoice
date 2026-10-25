"""EARS-98: ears-mail cuts the mouth's echo ANYWHERE in a turn, and says so.

RED on ears master bdb9e63 (repro-001): these tests state the WANTED behaviour.

On the MacBook speakers the mic hears the mouth. Today ears-mail cuts a mouth line only
when it LEADS the turn with at most 2 of his words ahead of it (``echo_prefix``,
``blocks[0].a > 2``) or begins at its very end (``echo_suffix``), judges candidacy by
``ts - (t1 - t0)`` (seconds late against the real start), and marks a cut nowhere a
seat can filter on. EARS-94 findings/route1-suppression.md (RQ1 case 1, RQ2 "What
reached a mail") measured the result: 7 turns mailed as the user's with an agent's words in
the body. The wanted behaviour, from the card and EARS-94 RQ3 option A:

- a mouth line's words are cut WHEREVER they sit in the turn: at its start with up to
  N of his words ahead of them (case 1 has 3), in its middle, or at its end;
- his own words are ALL kept: the unit of the cut is the echo SPAN, never the turn;
- a mail with a cut carries ``Ears-Echo: <seat>/<utt> <n> words cut`` (n = the words
  that line lost), and the seats mail keeps its "heard whole" section;
- candidacy is judged on the line's PLAY span (said - played_s .. said) against the
  turn's REAL span (stream start from the heartbeat, + t0 .. t1).

THE WORDS ARE SYNTHETIC; THE SHAPE IS REAL. Each fixture keeps a repro turn's shape, in
log order, trimmed to the fields ears-mail reads: the stream's listen-started and its
newest heartbeat before the turn, the saying/said rows of each mouth line near the turn,
and the turn. seq, t0/t1, utt, played_s, cut and device are as recorded. Every ts is
moved by ONE constant onto a synthetic 2030-06-09 base (EARS-393), so each delta, the ms
and the order are as recorded too. Every text is invented words, mapped word for word
(one invented word per real word, the same word each time it recurs, common short words
and the seat names kept), so each turn holds the same echo, the same mishearings and the
same words of his, at the same places. Each ECHO span below is the turn's own text for
that line (findings/repro.md). A span may keep at most LEFT_MAX of its words at its
seams: RQ2 counted a trim with at most 2 echo words left as "cut cleanly".

The AirPods control arm is turns heard while the output was the AirPods, where the
mic cannot hear the mouth: the user QUOTING a line he had just heard (EARS-94 RQ2
"False-positive check", the 41 looser-rule near-misses). Nothing of his may be cut
there. Two of them (152937, 169983) are cut TODAY, by echo_prefix: red on bdb9e63.
"""
import re

import pytest

from tests.test_ears_mail import run

LEFT_MAX = 2          # echo words a span may leave at its seams (RQ2: "cut cleanly")
ECHO_HEADER = re.compile(r"^\s*([a-z0-9._-]+)/([0-9a-f]+) (\d+) words cut\s*$")
DEVICE_RULES = {"seats": {"ivy": {"names": "ivy"}, "vera": {"names": "vera|vira"},
                          "walter": {"names": "walter"}}}


def words(text):
    """ears-mail's own word split (_words): lower-cased alphanumeric runs."""
    return "".join(c.lower() if c.isalnum() else " " for c in text).split()


# The 8 fixture turns on the MacBook speakers (EARS-94 RQ1 case 1 and RQ2), seq -> rows.
SPEAKERS = {
    188055: [
        {"ts": "2030-06-09T20:59:34.692+10:00", "seq": 187965, "kind": "event", "event": "listen-started", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper"},
        {"ts": "2030-06-09T20:59:35.134+10:00", "seq": 187966, "kind": "saying", "agent": "vera", "utt": "a97fd558c887", "device": "MacBook Pro Speakers", "text": "Tiba. Folepul ozodo the sepovuri uzo, and I'll luto didaval them. One isosa to aliku: on sepovuri the efa ilo vigi me, so if I ese vuvosa fil, that's ega."},
        {"ts": "2030-06-09T20:59:44.465+10:00", "seq": 187975, "kind": "said", "agent": "vera", "utt": "a97fd558c887", "device": "MacBook Pro Speakers", "played_s": 9.0, "cut": False, "text_played_est": "Tiba. Folepul ozodo the sepovuri uzo, and I'll luto didaval them. One isosa to aliku: on sepovuri the efa ilo vigi me, so if I ese vuvosa fil, that's ega."},
        {"ts": "2030-06-09T21:01:49.771+10:00", "seq": 188054, "kind": "event", "event": "heartbeat", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 135.0},
        {"ts": "2030-06-09T21:01:52.446+10:00", "seq": 188055, "kind": "turn", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "t0": 0.06, "t1": 133.99, "text": "Vigi me uzo. Folepul ozodo the sepovuri uzo, and I'll luto didaval them. One isosa to aliku, on sepovuri, the efa ilo vigi me. So if I ese vuvosa fil, that's ega. Okay. Ivari you. We'll uku idaf parerig. We egos to nad that. So that is, um, I ibesi that's the ukonimi that we egos to nad, geti atufanevudob. If it parerig, oru me adab. We pomito do an geti atufanevudob guroso. So, we pomito uku if we ilo ivu geti atufanevudob ilepo. I rimup you were ididol upobimegu amave, I otu't adab, nili Eziko ilo do it or abadurin. Um, I'm orov nageg's nili Eziko, um, you adab, ifev ekasu isosa that amod you do a asami elam. I ilo do it with opefe, dezalesi I rimup they iset their ezu ditelo, their ezu ekasu ditelo. So ukite that's ega I otu't adab. But, um, I'm orov it's obufatav. So uzo, for nili derevi, the ozugade have oli orupir fil. That's sobo badugon of uputofi or abadurin. I ileki't ufesome piramigu. Um, vuron, so, uh, so idaf I was ugakibop, idaf was, idaf was I sobo ididol nepebu, and I am omuga, imupifil, I'm omuga rego mebeba ki with duz dopov, and I'm ilepo to um, lul you, Oh yes, I did efel ki with an dibo. So, if we have, um, uh, if we have, uh, oli the elelal and, um, if ebogabisuz to the itife and the afag has a elam RI, upar imupifil, we nugisono otu't egos resi ebarutu to have idaf it's in nilef to badugon we odose sobo uvak at the vesa and the elam and the fakuzup and upar we'd have it."},
    ],
    158058: [
        {"ts": "2030-06-09T12:44:08.537+10:00", "seq": 157868, "kind": "event", "event": "listen-started", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper"},
        {"ts": "2030-06-09T12:48:42.382+10:00", "seq": 158032, "kind": "saying", "agent": "vera", "utt": "2d492fb06d3b", "device": "MacBook Pro Speakers", "text": "Yes. I kamusa dus that was Zeg's RIVO, but obuv badugon I zume't ovogek. The fakuzup efomuv akomulu eted the afag aroge to the nepeni efa. Ezezidar ega uzo."},
        {"ts": "2030-06-09T12:48:52.655+10:00", "seq": 158039, "kind": "said", "agent": "vera", "utt": "2d492fb06d3b", "device": "MacBook Pro Speakers", "played_s": 9.925, "cut": False, "text_played_est": "Yes. I kamusa dus that was Zeg's RIVO, but obuv badugon I zume't ovogek. The fakuzup efomuv akomulu eted the afag aroge to the nepeni efa. Ezezidar ega uzo."},
        {"ts": "2030-06-09T12:49:08.598+10:00", "seq": 158047, "kind": "event", "event": "heartbeat", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 300.0},
        {"ts": "2030-06-09T12:49:11.639+10:00", "seq": 158051, "kind": "saying", "agent": "vera", "utt": "6c681f74374e", "device": "MacBook Pro Speakers", "text": "Irufi it. Ivy's fakuzup atudid akomulu at three forty-one, eted the afag aroge to the nepeni efa. I've amozu Ivy to ligukaz it, and to teki it ononet the efa from uzo on."},
        {"ts": "2030-06-09T12:49:21.950+10:00", "seq": 158057, "kind": "said", "agent": "vera", "utt": "6c681f74374e", "device": "MacBook Pro Speakers", "played_s": 9.95, "cut": False, "text_played_est": "Irufi it. Ivy's fakuzup atudid akomulu at three forty-one, eted the afag aroge to the nepeni efa. I've amozu Ivy to ligukaz it, and to teki it ononet the efa from uzo on."},
        {"ts": "2030-06-09T12:49:24.355+10:00", "seq": 158058, "kind": "turn", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "t0": 243.15, "t1": 312.67, "text": "Hey, Vera, if you fiba otu't uku, if it fiba azevi't ive ano's atavelib, um, and you adab that we're in a areku fabonefipat, upar I rimup we pomito, uh, moru on that. That pomito be a rokubi asufin. And we do egos to teki the fakuzup zuniritugeguso ivam oli the vesa. Hey Vera? It's arapup zatu eted you're in the odiz ote, but it bul't be. Yeah. It'll sobo be... One isosa I've pedetuf otob. Yes, I kamusa dus that was Zeg's RIVO, but obuv badugon I zume't ovogek. The fakuzup otera akomulu eted the afag aroge to the nepeni efa, ezezidar ega uzo? Badugon... Vagu urida. Hey, Vera, um, eted a fakuzup bepe kekuzuz, it pomito oga the moru of the one, badugon in Epo's fotakulirireb, it davu, Zeg, and upar it davu, Nuzi. So eted it imifuran to Nuzi, ukite for rona ugim one, so it's not sobo Nuzi. Ivy fakuzup atudid akomulu at 341 eted the afag aroge to the nepeni, Nuzi. I've amozu Ivy to ligukaz it, and to teki it ononet the efa from uzo on."},
    ],
    158169: [
        {"ts": "2030-06-09T12:44:08.537+10:00", "seq": 157868, "kind": "event", "event": "listen-started", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper"},
        {"ts": "2030-06-09T12:51:45.079+10:00", "seq": 158139, "kind": "saying", "agent": "vera", "utt": "d60c8814374a", "device": "MacBook Pro Speakers", "text": "It is eru: the Eseti izod uzo gazeku at Aloruf Ogok. If your bipik riged modop the osi izod, ruda moli to emagire."},
        {"ts": "2030-06-09T12:51:52.687+10:00", "seq": 158144, "kind": "said", "agent": "vera", "utt": "d60c8814374a", "device": "MacBook Pro Speakers", "played_s": 7.325, "cut": False, "text_played_est": "It is eru: the Eseti izod uzo gazeku at Aloruf Ogok. If your bipik riged modop the osi izod, ruda moli to emagire."},
        {"ts": "2030-06-09T12:52:23.600+10:00", "seq": 158164, "kind": "event", "event": "heartbeat", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 495.0},
        {"ts": "2030-06-09T12:52:32.450+10:00", "seq": 158169, "kind": "turn", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "t0": 446.22, "t1": 501.85, "text": "So yeah, we're unekepo with our zefoke, but this is lom I'm gugukud uzo. Efet, teki a arevamorof. For that etav, the utaveni. Ilo you okosu it difo? Sobo a arevamorof. It is eru. Eseti izod uzo gazeku at Aloruf Ogok. If your bipik riged modop the osi izod, ruda moli to emagire. I odeg it in, lem the dirur of it in the bula. Eted you uvak at a bula, apu inas pomito be the bepe of it. Ogogalo sobo, oh, an asufin is the bepe, vuron? Okay, vege. You'd ukite teki that a ikukop epu zovega. Um, so that it's, or that's idopeval uvon. If you sobo had sobo the tepiv and sagi have the bula have a razapenu etav that's riged zileme. But for the utaveni, if you had that upofar, that ezelus odose imupifil idopeval efag 6 tepiv. Lom uzub donon have you egi? Lom uzub fedur. Uh Rivo, lom uzub Ozev fedur do we have at the ugisug?"},
    ],
    158485: [
        {"ts": "2030-06-09T12:44:08.537+10:00", "seq": 157868, "kind": "event", "event": "listen-started", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper"},
        {"ts": "2030-06-09T12:59:15.393+10:00", "seq": 158408, "kind": "saying", "agent": "vera", "utt": "d0d56a3fad92", "device": "MacBook Pro Speakers", "text": "Tiba. The ovoruv usevovi is in the Kulu areku, with a ugegi lom-to for Zeg and RIVO."},
        {"ts": "2030-06-09T12:59:21.623+10:00", "seq": 158412, "kind": "said", "agent": "vera", "utt": "d0d56a3fad92", "device": "MacBook Pro Speakers", "played_s": 5.9, "cut": False, "text_played_est": "Tiba. The ovoruv usevovi is in the Kulu areku, with a ugegi lom-to for Zeg and RIVO."},
        {"ts": "2030-06-09T13:01:08.602+10:00", "seq": 158477, "kind": "event", "event": "heartbeat", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 1020.0},
        {"ts": "2030-06-09T13:01:24.941+10:00", "seq": 158485, "kind": "turn", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "t0": 866.19, "t1": 1032.4, "text": "The ikonus takek. Idifo? I atudu the unusabol ezene. Oh, umop, umop, umop, umop. So you ine efomuv for pusog. We ine balavem afiluvu of ezomenum ekenot eluse. Yes. Oh, imupifil, okay, so we sobo ine mefal afiluvu. Yep. Yep, yep. And we isisi ine pavuzot. We were dizeb that, but we sozad't dizeb the pavuzot. Hey, Vera, so Zeg's sobo famatif me the, ilo I omug a kenal of that? Yeah. So Zeg's famatif me the madakat for Epo's aratot. I'll iruf you a kenal, Vera. Um, and he's egi idifo dudog um, efel ki with uh, the... Tiba? The Botadum's usevovi is in the Kulu areku with a ugegi lom-to for Zeg and T-ENA. Um, so, hey, Vera, so, Epo's aratot, I'm omuga idin it odid, uh, gok and so I've egi to lem this so it's sulof. Um, we'll ku gok, gok, and ilipe. I'll idin the aratot isosa. So he's egi nili zaguroko for Zeg to uvak at and is fasunuzar emib, erub, aratot biremebe and dopov, and upar nageg's the ditul for resi of them, and it isuve mulu the akon as Seso and your idifo. Lom are your efibu adimapale? You've egi idifo, and upar we've egi isanupupo of idifo odid nudiguv fero idifo? Um, and upar we've egi emib. And we've egi erub. And we've egi uh, nili upuko zefoke and it's oli lutuk her gesi. So, Vera, they're not dizeb otera agap, they're dizeb pavuzot, and I rimup we pomito teki a losi for sadit, not dafeg midag the kanip's dusif. I rimup, Vera, we pomito teki a losi to, um, ine pavuzot for oli our ogorebod and, um, ukite that ofup, yeah, ukite pavuzot afiluvu of dizeb the the Agozedo, the otera upezo, nugisono. Sagi, my, my, I titana my isosa. I titana my and uzo it's obovegi not keru. It azevi't otaz rona to oru me not rego pesat that. I odose sobo dotu it, but it is obovegi it, so it isisi lurig. So, Dudabilit that's okay. And you ilo be bodip uzo, Vera, ubetif you egi upobimegu agibusadi."},
    ],
    159388: [
        {"ts": "2030-06-09T13:32:52.958+10:00", "seq": 159311, "kind": "event", "event": "listen-started", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper"},
        {"ts": "2030-06-09T13:34:03.974+10:00", "seq": 159333, "kind": "saying", "agent": "vera", "utt": "d3777a4c51fa", "device": "MacBook Pro Speakers", "text": "I'm otob. Dosezip ki lom we did the ifi geki vesa."},
        {"ts": "2030-06-09T13:34:07.784+10:00", "seq": 159335, "kind": "said", "agent": "vera", "utt": "d3777a4c51fa", "device": "MacBook Pro Speakers", "played_s": 3.55, "cut": False, "text_played_est": "I'm otob. Dosezip ki lom we did the ifi geki vesa."},
        {"ts": "2030-06-09T13:34:46.500+10:00", "seq": 159360, "kind": "saying", "agent": "vera", "utt": "5a701f1aae7c", "device": "MacBook Pro Speakers", "text": "Gabugo the duk's Padukimor uzo. Idipas biko't kesobo, so I'm abapamog for it."},
        {"ts": "2030-06-09T13:34:51.703+10:00", "seq": 159364, "kind": "said", "agent": "vera", "utt": "5a701f1aae7c", "device": "MacBook Pro Speakers", "played_s": 4.9, "cut": False, "text_played_est": "Gabugo the duk's Padukimor uzo. Idipas biko't kesobo, so I'm abapamog for it."},
        {"ts": "2030-06-09T13:35:23.023+10:00", "seq": 159383, "kind": "event", "event": "heartbeat", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 150.0},
        {"ts": "2030-06-09T13:35:31.655+10:00", "seq": 159388, "kind": "turn", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "t0": 68.43, "t1": 155.56, "text": "Onel uruna geki vesa. I'm otob dosezip ki lom we did the ifi geki vesa. We did it didaval the uvon. Hey, Vera, we did it didaval Uzido didaval the bipik, but idaf I'm ugakibop uzo is that ukite I anor in the gorogebo. Nure, I did. And upar ukite I ku, uh, okolokak, um, tadug, no iton, okolokak, um, and upar ukite we ilo elavudo. Oh yeah, it's his duk. So ilo you sev to luto dus the duk? It davu duk. So, 0 no, it sobo davu lidupev. Okay. So do we have a an novuri to have... Gabugo the duk's Padukimor uzo. Situpi biko't kesobo, so I'm abapamog for it. Yeah, we orupir it fil. It was biv biv. So, we orupir fil Padukimor, but Zeg's omuga moru the Padukimor difo on. Um, Padukimor. I'll do it. Padukimor. Yep, duk duk egi it. Yeah, we had orupir fil the, we had orupir fil the Padukimor, but I've orupir the Padukimor difo on. Uzo, I ilo sisu Padukimor ekasu, F2IM otoga ABEPA. geki vesa. Yes. Yeah, we did it. Geki vesa we were dizeb Uzido. Hey, Ofavem. So do I itir to have Padukimor ekasu, Z2, ZE, ABEPA, or sobo Padukimor?"},
    ],
    152161: [
        {"ts": "2030-06-09T08:02:00.580+10:00", "seq": 151940, "kind": "event", "event": "listen-started", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper"},
        {"ts": "2030-06-09T08:07:40.817+10:00", "seq": 152147, "kind": "saying", "agent": "vera", "utt": "4a3c6d29f0cb", "device": "MacBook Pro Speakers", "text": "Yes, Keroga. Ipenem Esodale, a pulimafenir in Eganitapa. His Olugetufamat onom amave ninety thousand tepiv and deditu him eguno tifede seventy esipa."},
        {"ts": "2030-06-09T08:07:51.052+10:00", "seq": 152154, "kind": "said", "agent": "vera", "utt": "4a3c6d29f0cb", "device": "MacBook Pro Speakers", "played_s": 9.975, "cut": False, "text_played_est": "Yes, Keroga. Ipenem Esodale, a pulimafenir in Eganitapa. His Olugetufamat onom amave ninety thousand tepiv and deditu him eguno tifede seventy esipa."},
        {"ts": "2030-06-09T08:08:00.639+10:00", "seq": 152159, "kind": "event", "event": "heartbeat", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 360.0},
        {"ts": "2030-06-09T08:08:03.909+10:00", "seq": 152161, "kind": "turn", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "t0": 340.59, "t1": 361.27, "text": "Yes, Keroga. Usufuvub Ukaka, a pulimafenir in Ofobapet. His Apupe keso onom amave 90,000 tepiv, and deditu him eguno tifede 70 esipa. Oh, ledo on, Ideb... And they leleged that keru. Ilo you be bodip, evinet, Vera? He's bogazil the madakat's aratot isosa."},
    ],
    152828: [
        {"ts": "2030-06-09T08:02:00.580+10:00", "seq": 151940, "kind": "event", "event": "listen-started", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper"},
        {"ts": "2030-06-09T08:35:59.633+10:00", "seq": 152800, "kind": "saying", "agent": "vera", "utt": "7f0c313ce4b0", "device": "MacBook Pro Speakers", "text": "Ivy irufi it: Aloruf's Gole Okesuzafa Egudag. Podilaz of mapeboda, mulu our telonut. Vasatelene: it zarov in ivafo; we gebi and osolurika."},
        {"ts": "2030-06-09T08:36:08.727+10:00", "seq": 152807, "kind": "said", "agent": "vera", "utt": "7f0c313ce4b0", "device": "MacBook Pro Speakers", "played_s": 8.675, "cut": False, "text_played_est": "Ivy irufi it: Aloruf's Gole Okesuzafa Egudag. Podilaz of mapeboda, mulu our telonut. Vasatelene: it zarov in ivafo; we gebi and osolurika."},
        {"ts": "2030-06-09T08:36:33.190+10:00", "seq": 152823, "kind": "event", "event": "heartbeat", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 2070.0},
        {"ts": "2030-06-09T08:36:41.290+10:00", "seq": 152828, "kind": "turn", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "t0": 2036.79, "t1": 2076.1, "text": "Ivy irufi it Aloruf's Gole Okesuzafa Egudag. Podilaz of Mapeboda, mulu our akus udu. Vasatelene, it zarov in ivafo. We gebi and osolurika. Oh, yeah. I rimup we irufi that nepebu, Aloruf's gole okesuzafa isosa. Um, but Aloruf sagi onut ki with their epigasosil avafugod, vuron? Mulu, Aloruf efel ki with zefoke, but it's idopeval tiba rona for usiso ugim odeg, at ivipo I rimup it'll be adag otubegeda ano oga ki fodibo it, atol if it is the sotegodi, uh, dibo of one of the umako bapobol."},
    ],
    152799: [
        {"ts": "2030-06-09T08:02:00.580+10:00", "seq": 151940, "kind": "event", "event": "listen-started", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper"},
        {"ts": "2030-06-09T08:34:32.105+10:00", "seq": 152744, "kind": "saying", "agent": "vera", "utt": "107393b7693e", "device": "MacBook Pro Speakers", "text": "Yep, sekevume uzo, nine percent."},
        {"ts": "2030-06-09T08:34:35.321+10:00", "seq": 152747, "kind": "said", "agent": "vera", "utt": "107393b7693e", "device": "MacBook Pro Speakers", "played_s": 2.75, "cut": False, "text_played_est": "Yep, sekevume uzo, nine percent."},
        {"ts": "2030-06-09T08:35:48.189+10:00", "seq": 152793, "kind": "event", "event": "heartbeat", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 2025.0},
        {"ts": "2030-06-09T08:35:59.398+10:00", "seq": 152799, "kind": "turn", "source": "mic", "device": "MacBook Pro Microphone", "session": "ears-keeper", "t0": 1949.37, "t1": 2033.17, "text": "Yep, sekevume uzo, 9%. Yep. Vepalis. Vepalis. And, uh, hey, so odose you lul Ivy lom she lami with the tatebota amave idaf that ivo noke. Um, eru amave, I rimup, gole aratot or upobimegu mulu that. Um, and, uh, yeah, ilo you uvak him ki as nure? You've egi the alepana. Have a uvak idaf he's udegodaru. He's pesat Oko, so he's idopeval udegodaru. And uku if he's egi uga, uku idaf his Dakata is. Sagi, he kabovi to adab my riso edu, so odose you pigit a ebarutu to him and so I rimup his Oko or Oko igenaraka in BI. He's, I rimup he's rona nufevobobo in supoko tesutotad BI, and esulit and ezene onuvem ugim sobo an osi kobeba pifidor ukegubam nagudi that's not upepulavif amave it. He lubabagize ikit otaz, he usigo onel nufevobobo in idaf we kiroso him, and it isisi be an valovisuros to elavudo with, with him. Um, if he's in this dalik, upar we rureluz teki ezene ki totopomif to egos. But it odose be zileme, odose have a lekikug, a ivulumobup in the ovug."},
    ],
}


# The AirPods control arm: his quotes of a line he had just heard, seq -> rows.
AIRPODS = {
    148037: [
        {"ts": "2030-06-09T00:00:00.517+10:00", "seq": 146378, "kind": "event", "event": "listen-started", "source": "mic", "device": "airpods", "session": "ears-keeper"},
        {"ts": "2030-06-09T01:20:35.989+10:00", "seq": 148022, "kind": "saying", "agent": "vera", "utt": "f5522ed9688e", "device": "airpods", "text": "They're the elam-eko's relonide bibegig, from the Zeg elam. Soked oler to your Iroritu and Visebinov at viru, so the afag vigi the elam ote. That agap was a one-fil bofem-ki afole Asetuvo."},
        {"ts": "2030-06-09T01:20:47.192+10:00", "seq": 148024, "kind": "said", "agent": "vera", "utt": "f5522ed9688e", "device": "airpods", "played_s": 10.875, "cut": False, "text_played_est": "They're the elam-eko's relonide bibegig, from the Zeg elam. Soked oler to your Iroritu and Visebinov at viru, so the afag vigi the elam ote. That agap was a one-fil bofem-ki afole Asetuvo."},
        {"ts": "2030-06-09T01:21:06.886+10:00", "seq": 148035, "kind": "event", "event": "heartbeat", "source": "mic", "device": "airpods", "session": "ears-keeper", "stream_s": 4860.0},
        {"ts": "2030-06-09T01:21:09.126+10:00", "seq": 148037, "kind": "turn", "source": "mic", "device": "airpods", "session": "ears-keeper", "t0": 4842.12, "t1": 4860.25, "text": "Okay, eted you ive that agap was a one fil bofem ki afole Asetuvo, idaf do you imav by that? And ega did I uku is Iroritu ifas aresi gunu, 16 riginik, onite, ekasu elavudo, Iroritu, and aresi gunu? Idaf ikit that imav?"},
    ],
    166412: [
        {"ts": "2030-06-09T19:05:23.241+10:00", "seq": 164113, "kind": "event", "event": "listen-started", "source": "mic", "device": "airpods", "session": "ears-keeper"},
        {"ts": "2030-06-09T20:55:25.522+10:00", "seq": 166405, "kind": "saying", "agent": "vera", "utt": "ab3c2803512a", "device": "airpods", "text": "Eroliv fok, but one zit't. A zun oba giba in a uziran tena ilo uni nage as you. The kuvuli otora that one unop, upar its uzadaba davu zero unop."},
        {"ts": "2030-06-09T20:55:35.396+10:00", "seq": 166406, "kind": "said", "agent": "vera", "utt": "ab3c2803512a", "device": "airpods", "played_s": 9.45, "cut": False, "text_played_est": "Eroliv fok, but one zit't. A zun oba giba in a uziran tena ilo uni nage as you. The kuvuli otora that one unop, upar its uzadaba davu zero unop."},
        {"ts": "2030-06-09T20:55:38.372+10:00", "seq": 166407, "kind": "event", "event": "heartbeat", "source": "mic", "device": "airpods", "session": "ears-keeper", "stream_s": 6615.0},
        {"ts": "2030-06-09T20:55:46.253+10:00", "seq": 166412, "kind": "turn", "source": "mic", "device": "airpods", "session": "ears-keeper", "t0": 6613.23, "t1": 6620.89, "text": "Idimifagira. Okay. So idaf is it? A zun L giba in a uziran tena ilo uni nage as me."},
    ],
    165346: [
        {"ts": "2030-06-09T19:05:23.241+10:00", "seq": 164113, "kind": "event", "event": "listen-started", "source": "mic", "device": "airpods", "session": "ears-keeper"},
        {"ts": "2030-06-09T20:02:01.960+10:00", "seq": 165340, "kind": "saying", "agent": "vera", "utt": "318b482407be", "device": "airpods", "text": "Ekasu, not ilifu. Eted nageg's a osodo, it lomuso the geki eba aneloku: ikit that soked ikesased?"},
        {"ts": "2030-06-09T20:02:08.360+10:00", "seq": 165341, "kind": "event", "event": "heartbeat", "source": "mic", "device": "airpods", "session": "ears-keeper", "stream_s": 3405.0},
        {"ts": "2030-06-09T20:02:08.478+10:00", "seq": 165342, "kind": "said", "agent": "vera", "utt": "318b482407be", "device": "airpods", "played_s": 6.0, "cut": False, "text_played_est": "Ekasu, not ilifu. Eted nageg's a osodo, it lomuso the geki eba aneloku: ikit that soked ikesased?"},
        {"ts": "2030-06-09T20:02:15.880+10:00", "seq": 165346, "kind": "turn", "source": "mic", "device": "airpods", "session": "ears-keeper", "t0": 3406.29, "t1": 3410.53, "text": "Yeah, and eted you ive it lomuso the geki eba aneloku, idaf's that midir on?"},
    ],
    160781: [
        {"ts": "2030-06-09T13:35:56.724+10:00", "seq": 159396, "kind": "event", "event": "listen-started", "source": "mic", "device": "airpods", "session": "ears-keeper"},
        {"ts": "2030-06-09T14:21:42.199+10:00", "seq": 160776, "kind": "event", "event": "heartbeat", "source": "mic", "device": "airpods", "session": "ears-keeper", "stream_s": 2745.0},
        {"ts": "2030-06-09T14:21:45.803+10:00", "seq": 160777, "kind": "saying", "agent": "vera", "utt": "d1ae65c32842", "device": "airpods", "text": "Ipabo etil, in a piseda giba in your ebik gusilozad, rapur 2023. Izudomig kimise an novuri odefe that feva, and the epulebe from daporob 2.40, in Zukogeki 2023. Liku's in the izudomig. And odevirira: that lekukoso was the kanip enobap me from ivif. Ogimi vesa."},
        {"ts": "2030-06-09T14:21:55.159+10:00", "seq": 160781, "kind": "turn", "source": "mic", "device": "airpods", "session": "ears-keeper", "t0": 2751.81, "t1": 2755.96, "text": "Oh, rapur 2023, ipabo etil in your ebik gusilozad."},
        {"ts": "2030-06-09T14:22:00.839+10:00", "seq": 160784, "kind": "said", "agent": "vera", "utt": "d1ae65c32842", "device": "airpods", "played_s": 14.63, "cut": True, "text_played_est": "Ipabo etil, in a piseda giba in your ebik gusilozad, rapur 2023. Izudomig kimise an novuri odefe that feva, and the epulebe from daporob 2.40, in Zukogeki 2023. Liku's in the izudomig. And odevirira: that lekukoso"},
    ],
    152937: [
        {"ts": "2030-06-09T08:40:20.572+10:00", "seq": 152892, "kind": "event", "event": "listen-started", "source": "mic", "device": "airpods", "session": "ears-keeper"},
        {"ts": "2030-06-09T08:41:36.445+10:00", "seq": 152928, "kind": "saying", "agent": "vera", "utt": "e0239d6ce55b", "device": "airpods", "text": "Yes, alope amonu. Atepego oge amave two-uzumed of our kudo obetu. Taso's bisonu: agap uzo mumelos with the erub. The nubo adepe on one lekukoso for you."},
        {"ts": "2030-06-09T08:41:46.224+10:00", "seq": 152931, "kind": "said", "agent": "vera", "utt": "e0239d6ce55b", "device": "airpods", "played_s": 9.35, "cut": False, "text_played_est": "Yes, alope amonu. Atepego oge amave two-uzumed of our kudo obetu. Taso's bisonu: agap uzo mumelos with the erub. The nubo adepe on one lekukoso for you."},
        {"ts": "2030-06-09T08:41:50.751+10:00", "seq": 152936, "kind": "event", "event": "heartbeat", "source": "mic", "device": "airpods", "session": "ears-keeper", "stream_s": 90.0},
        {"ts": "2030-06-09T08:41:51.671+10:00", "seq": 152937, "kind": "turn", "source": "mic", "device": "airpods", "session": "ears-keeper", "t0": 86.46, "t1": 88.93, "text": "Oh, ledo on. I egi one lekukoso for you."},
    ],
    186568: [
        {"ts": "2030-06-09T18:18:31.937+10:00", "seq": 184309, "kind": "event", "event": "listen-started", "source": "mic", "device": "airpods", "session": "ears-keeper"},
        {"ts": "2030-06-09T19:55:51.485+10:00", "seq": 186551, "kind": "saying", "agent": "vera", "utt": "bffc49e3f854", "device": "airpods", "text": "Dudeluti on this pifidor. The Ogab upute ebobe udu. Defu is the marakup's ezu udu, so the lul epog with its ivam."},
        {"ts": "2030-06-09T19:55:59.442+10:00", "seq": 186552, "kind": "said", "agent": "vera", "utt": "bffc49e3f854", "device": "airpods", "played_s": 7.425, "cut": False, "text_played_est": "Dudeluti on this pifidor. The Ogab upute ebobe udu. Defu is the marakup's ezu udu, so the lul epog with its ivam."},
        {"ts": "2030-06-09T19:56:17.964+10:00", "seq": 186564, "kind": "event", "event": "heartbeat", "source": "mic", "device": "airpods", "session": "ears-keeper", "stream_s": 5865.0},
        {"ts": "2030-06-09T19:56:24.566+10:00", "seq": 186568, "kind": "turn", "source": "mic", "device": "airpods", "session": "ears-keeper", "t0": 5847.36, "t1": 5869.6, "text": "Okay, so you okokotofi biko't vigi that I sobo noke that or you did? Badugon I noke idopeval sobo the marakup udu for the marakup they're gugukud on. Did you uku that nepebu you ufina, defu is the marakup's ezu udu, so the usi epog with its ivam? Badugon it aroz't epero from your anibasak that you had sesi that ebarutu."},
    ],
    169497: [
        {"ts": "2030-06-09T06:23:44.228+10:00", "seq": 168954, "kind": "event", "event": "listen-started", "source": "mic", "device": "airpods", "session": "ears-keeper"},
        {"ts": "2030-06-09T06:49:35.780+10:00", "seq": 169470, "kind": "saying", "agent": "vera", "utt": "316c1d68912c", "device": "airpods", "text": "Surin ki: I'm at my tonizen azupo, so I'm olususo okim to a mapim me in a eba mubevam. Uvav azorepi: the efib me tegak it oli."},
        {"ts": "2030-06-09T06:49:43.814+10:00", "seq": 169474, "kind": "said", "agent": "vera", "utt": "316c1d68912c", "device": "airpods", "played_s": 7.625, "cut": False, "text_played_est": "Surin ki: I'm at my tonizen azupo, so I'm olususo okim to a mapim me in a eba mubevam. Uvav azorepi: the efib me tegak it oli."},
        {"ts": "2030-06-09T06:50:14.306+10:00", "seq": 169494, "kind": "event", "event": "heartbeat", "source": "mic", "device": "airpods", "session": "ears-keeper", "stream_s": 1590.0},
        {"ts": "2030-06-09T06:50:17.067+10:00", "seq": 169497, "kind": "turn", "source": "mic", "device": "airpods", "session": "ears-keeper", "t0": 1560.6, "t1": 1590.76, "text": "Vagu dibo. Vagu dibo. And sagi, teraz in your tonizen do you have that imab badugon you ive that, you ive, mulu, uvav azorepi the efib me, tegak it oli. That's imupifil asaze. I rimup that's a asaze isosa that, yeah, we pomito lem in the akiza for asami okame so that esulit adab that that's the usum. It idopeval azevi't egos to be ludanenosi to ive that, but pomito adab it and ilo ive it if pabekozoful."},
    ],
    169983: [
        {"ts": "2030-06-09T06:23:44.228+10:00", "seq": 168954, "kind": "event", "event": "listen-started", "source": "mic", "device": "airpods", "session": "ears-keeper"},
        {"ts": "2030-06-09T07:19:58.330+10:00", "seq": 169969, "kind": "saying", "agent": "vera", "utt": "c3d918d449f0", "device": "airpods", "text": "Asufo one: the Visebinov ugazu is zozabus zenen. It uzo isife bodip ubuda a elam is gepanaz didaval Visebinov. Bosav it and ebavun it on?"},
        {"ts": "2030-06-09T07:20:06.970+10:00", "seq": 169973, "kind": "said", "agent": "vera", "utt": "c3d918d449f0", "device": "airpods", "played_s": 8.15, "cut": False, "text_played_est": "Asufo one: the Visebinov ugazu is zozabus zenen. It uzo isife bodip ubuda a elam is gepanaz didaval Visebinov. Bosav it and ebavun it on?"},
        {"ts": "2030-06-09T07:20:14.314+10:00", "seq": 169977, "kind": "event", "event": "heartbeat", "source": "mic", "device": "airpods", "session": "ears-keeper", "stream_s": 3390.0},
        {"ts": "2030-06-09T07:20:24.754+10:00", "seq": 169983, "kind": "turn", "source": "mic", "device": "airpods", "session": "ears-keeper", "t0": 3383.16, "t1": 3398.44, "text": "Yeah, bosav it and ebavun it on, but upar, zinur, I utomak that epu you noke amave the nimuzi umite. Idaf was the nimuzi mulu? Idaf was the isosa for that? Was it to have avubigatam ukigo or to or upobimegu ones?"},
    ],
}


# seq -> [(utt, the turn's text for that mouth line)]: the echo, read by eye against the
# line. Everything else in the turn is his. See findings/repro.md for each turn's story.
ECHO = {
    # case 1: 3 words of his, then her whole line, then 2 minutes
    # of his. Today: nothing cut (3 > 2).
    188055: [("a97fd558c887", "Folepul ozodo the sepovuri uzo, and I'll luto didaval them. "
                              "One isosa to aliku, on sepovuri, the efa ilo vigi me. So if "
                              "I ese vuvosa fil, that's ega.")],
    # mid-turn twice: her line at 31 s into his 70 s turn, her next at its very end
    # (whisper dropped its first two words, heard "efa" as "Nuzi"). Today: 30 echo words kept.
    158058: [("2d492fb06d3b", "Yes, I kamusa dus that was Zeg's RIVO, but obuv badugon I "
                              "zume't ovogek. The fakuzup otera akomulu eted the afag "
                              "aroge to the nepeni efa, ezezidar ega uzo?"),
             ("6c681f74374e", "Ivy fakuzup atudid akomulu at 341 eted the afag aroge to "
                              "the nepeni, Nuzi. I've amozu Ivy to ligukaz it, and to teki "
                              "it ononet the efa from uzo on.")],
    # mid-turn: her line 10.5 s into his turn, 33 of his words ahead. Today: 22 kept.
    158169: [("d60c8814374a", "It is eru. Eseti izod uzo gazeku at Aloruf Ogok. If your "
                              "bipik riged modop the osi izod, ruda moli to emagire.")],
    # mid-turn: 41 s into a 166 s turn, 105 of his words ahead. Today: 16 kept.
    158485: [("d0d56a3fad92", "Tiba? The Botadum's usevovi is in the Kulu areku with a "
                              "ugegi lom-to for Zeg and T-ENA.")],
    # near the start (4 of his words ahead, the line began 2.8 s into the turn) and
    # mid-turn (45 s in). Today: 15 kept.
    159388: [("d3777a4c51fa", "I'm otob dosezip ki lom we did the ifi geki vesa."),
             ("5a701f1aae7c", "Gabugo the duk's Padukimor uzo. Situpi biko't kesobo, so "
                              "I'm abapamog for it.")],
    # leading, misheard names and numerals ("Usufuvub Ukaka" for "Ipenem Esodale",
    # "Ofobapet", "90,000", "70"): a partial trim today, 8 echo words kept.
    152161: [("4a3c6d29f0cb", "Yes, Keroga. Usufuvub Ukaka, a pulimafenir in Ofobapet. His "
                              "Apupe keso onom amave 90,000 tepiv, and deditu him eguno "
                              "tifede 70 esipa.")],
    # leading, "akus udu" for "telonut": a partial trim today, 4 echo words kept.
    152828: [("7f0c313ce4b0", "Ivy irufi it Aloruf's Gole Okesuzafa Egudag. Podilaz of "
                              "Mapeboda, mulu our akus udu. Vasatelene, it zarov in ivafo. "
                              "We gebi and osolurika.")],
}

# 152799: "Yep, sekevume uzo, 9%." for "Yep, sekevume uzo, nine
# percent." Numerals are word-matching's known blind spot (RQ2 misses, RQ3 "does NOT
# cover"). Today it cuts "Yep, sekevume uzo" and leaves "9%", with no header.
NUMERALS_SEQ, NUMERALS_UTT = 152799, "107393b7693e"
NUMERALS_ECHO = "Yep, sekevume uzo, 9%."


def rows_of(seq):
    return SPEAKERS.get(seq) or AIRPODS[seq]


def turn_of(seq):
    return next(r for r in rows_of(seq) if r.get("kind") == "turn")


def spans(seq):
    """[(utt, first word, end word)] of each echo span, as word indices of the turn."""
    text = turn_of(seq)["text"]
    out = []
    for utt, echo in ECHO[seq]:
        at = text.index(echo)
        a = len(words(text[:at]))
        out.append((utt, a, a + len(words(echo))))
    return out


def his_words(seq):
    """The turn's words that are his: every word outside the echo spans, in order."""
    w = words(turn_of(seq)["text"])
    cut = {i for _, a, b in spans(seq) for i in range(a, b)}
    return [x for i, x in enumerate(w) if i not in cut]


def is_subsequence(small, big):
    it = iter(big)
    return all(x in it for x in small)


def echo_entries(headers):
    """[(seat, utt, n)] from Ears-Echo: one header per line or a comma list (fix-001's
    choice), each entry '<seat>/<utt> <n> words cut'. [] if absent."""
    v = headers.get("Ears-Echo")
    if not v:
        return []
    parts = v if isinstance(v, list) else str(v).split(",")
    out = []
    for p in parts:
        m = ECHO_HEADER.match(p)
        assert m, f"Ears-Echo entry {p!r} is not '<seat>/<utt> <n> words cut'"
        out.append((m.group(1), m.group(2), int(m.group(3))))
    return out


def device_mail(seq):
    """The device mail (the live path, --to device) ears-mail sends for SEQ's turn."""
    mails = [m for m in run(rows_of(seq), DEVICE_RULES, to=None) if m["seq"] == seq]
    assert len(mails) == 1, f"seq {seq}: his words must be mailed, got {len(mails)} mails"
    return mails[0]["mail"]


def body_words(mail):
    return words(mail["body"])


# -- the fixtures are what they say they are -----------------------------------

@pytest.mark.parametrize("seq", sorted(SPEAKERS) + sorted(AIRPODS))
def test_fixture_rows_are_in_log_order_and_hold_one_turn(seq):
    rows = rows_of(seq)
    assert [r["seq"] for r in rows] == sorted(r["seq"] for r in rows)
    assert [r["seq"] for r in rows if r["kind"] == "turn"] == [seq]   # rows after it: still playing
    assert any(r.get("event") == "heartbeat" for r in rows)       # the stream start is known


@pytest.mark.parametrize("seq", sorted(ECHO))
def test_fixture_echo_spans_are_the_turns_own_text_and_its_lines(seq):
    text = turn_of(seq)["text"]
    utts = {r["utt"] for r in rows_of(seq) if r.get("kind") == "said"}
    for utt, echo in ECHO[seq]:
        assert text.count(echo) == 1 and utt in utts
    assert his_words(seq)                                         # a MIXED turn: he spoke


# -- the echo is cut wherever it sits; every word of his is kept ----------------

@pytest.mark.parametrize("seq", sorted(ECHO))
def test_every_word_of_his_is_kept(seq):
    """Cut the echo SPAN, never the turn: his words reach the mail, in order."""
    mail = device_mail(seq)
    assert is_subsequence(his_words(seq), body_words(mail)), mail["body"]


@pytest.mark.parametrize("seq", sorted(ECHO))
def test_the_echo_is_cut_wherever_it_sits(seq):
    """At most LEFT_MAX of each span's words survive (at its seams); today 4-31 do."""
    mail = device_mail(seq)
    left = len(body_words(mail)) - len(his_words(seq))
    assert left <= LEFT_MAX * len(ECHO[seq]), (
        f"{left} echo words still in the body of seq {seq}: {mail['body']!r}")


@pytest.mark.parametrize("seq", sorted(ECHO))
def test_a_cut_is_flagged_per_line_with_its_count(seq):
    """Ears-Echo names each line that was cut, as <seat>/<utt>, and the words it lost;
    the counts add up to the words the body lost."""
    mail = device_mail(seq)
    got = echo_entries(mail["headers"])
    assert sorted(u for _, u, _ in got) == sorted(u for u, _, _ in spans(seq)), mail["headers"]
    assert all(seat == "vera" for seat, _, _ in got)              # the line's own agent
    want = {u: b - a for u, a, b in spans(seq)}
    for _, utt, n in got:
        assert want[utt] - LEFT_MAX <= n <= want[utt] + LEFT_MAX
    assert sum(n for _, _, n in got) == len(words(turn_of(seq)["text"])) - len(body_words(mail))


@pytest.mark.parametrize("seq", sorted(ECHO))
def test_a_cut_leaves_the_speaker_alone(seq):
    """speaker_of() is EARS-110's; the cut leaves Ears-Speaker alone unless the turn's
    label names a seat whose line was cut (EARS-467, heard_rec: then the words left are
    the user's and get the default; tests/test_echo_speaker.py). These fixtures carry no
    label, so the same turn with no mouth line in the room (nothing to cut) mints the
    speaker the cut must keep, whatever speaker_of's default is (theo@ before EARS-110,
    unknown@ since)."""
    mail = device_mail(seq)
    assert echo_entries(mail["headers"]), "the fixture must be cut for this to mean anything"
    bare = [r for r in rows_of(seq) if r.get("kind") not in ("saying", "said")]
    alone = [m for m in run(bare, DEVICE_RULES, to=None) if m["seq"] == seq]
    assert len(alone) == 1 and "Ears-Echo" not in alone[0]["mail"]["headers"]
    assert mail["headers"]["Ears-Speaker"] == alone[0]["mail"]["headers"]["Ears-Speaker"]


# Fixtures whose turn OPENS with his words: a seat named for his first word
# (name_first) is mailed on the seats path whatever the cut, so its body can be read.
HIS_FIRST = {188055: 'vigi', 158058: 'hey', 158169: 'so', 158485: 'the', 159388: 'onel'}


@pytest.mark.parametrize("seq", sorted(HIS_FIRST))
def test_the_seats_mail_keeps_the_heard_whole_section(seq):
    """--to seats: his words first, then the whole turn under the existing marker, and
    the same Ears-Echo. (The device mail drops "heard whole" by design, review S1,
    test_device_echo_of_the_mouth_is_not_his_turn; see findings/repro.md.)"""
    rules = {"seats": {"probe": {"names": HIS_FIRST[seq], "name_first": True}}}
    mails = [m for m in run(rows_of(seq), rules, to="seats") if m["seq"] == seq]
    assert len(mails) == 1
    mail, whole = mails[0]["mail"], turn_of(seq)["text"]
    his, sep, rest = mail["body"].partition("\n-- heard whole, echo included --\n")
    assert sep and rest == whole + "\n", mail["body"][-300:]
    assert is_subsequence(his_words(seq), words(his))
    assert len(words(his)) - len(his_words(seq)) <= LEFT_MAX * len(ECHO[seq])
    assert sorted(u for _, u, _ in echo_entries(mail["headers"])) == sorted(u for u, _ in ECHO[seq])


# -- timing: the line's play span against the turn's REAL span ------------------

def test_a_late_turn_row_is_judged_by_the_turns_real_start():
    """The turn row lands 7 s after his last word (redecode; 13 of 1,748 turns on 06-09/10
    lagged > 5 s, max 7.9). ts - (t1 - t0) then starts the turn after the line's end +
    TAIL_S, so today the line is no candidate; on the real span it opens the turn."""
    line = "The build is green and the branch is pushed to the server."
    his = "Okay, so merge it after lunch and tell Ivy."
    s = "2030-06-09T12:00:%06.3f+10:00"
    rows = [
        {"ts": s % 0.0, "seq": 1, "kind": "event", "event": "listen-started", "source": "mic",
         "device": "MacBook Pro Microphone", "session": "ears-keeper"},
        {"ts": s % 15.0, "seq": 2, "kind": "event", "event": "heartbeat", "source": "mic",
         "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 15.0},
        {"ts": s % 20.0, "seq": 3, "kind": "saying", "agent": "vera", "utt": "c0ffee000001",
         "device": "MacBook Pro Speakers", "text": line},
        {"ts": s % 23.0, "seq": 4, "kind": "said", "agent": "vera", "utt": "c0ffee000001",
         "device": "MacBook Pro Speakers", "played_s": 2.8, "cut": False, "text_played_est": line},
        {"ts": s % 33.0, "seq": 5, "kind": "turn", "source": "mic", "device": "MacBook Pro Microphone",
         "session": "ears-keeper", "t0": 20.0, "t1": 26.0, "text": line + " " + his},
    ]
    mails = run(rows, DEVICE_RULES, to=None)
    assert [m["seq"] for m in mails] == [5]
    assert mails[0]["mail"]["body"] == his + "\n"
    assert echo_entries(mails[0]["mail"]["headers"]) == [("vera", "c0ffee000001", len(words(line)))]


# -- 152799: numerals, the known blind spot --------------------------------------

def test_numerals_none_of_his_words_are_cut():
    """'9%' is not 'nine percent' to a word matcher. At least: every word of his after
    the echo survives, and a cut, if any, is flagged with its true count."""
    mail = device_mail(NUMERALS_SEQ)
    text = turn_of(NUMERALS_SEQ)["text"]
    assert text.startswith(NUMERALS_ECHO)
    his = words(text[len(NUMERALS_ECHO):])
    assert is_subsequence(his, body_words(mail)), mail["body"]
    lost = len(words(text)) - len(body_words(mail))
    got = echo_entries(mail["headers"])
    if lost:
        assert got == [("vera", NUMERALS_UTT, lost)], mail["headers"]
    else:
        assert got == []


@pytest.mark.xfail(reason="numerals: '9%' vs 'nine percent' falls through word matching "
                          "(EARS-94 RQ2 misses; RQ3 'does NOT cover'); a stronger cut needs "
                          "number normalisation, not in this card", strict=False)
def test_numerals_the_whole_echo_is_cut():
    mail = device_mail(NUMERALS_SEQ)
    text = turn_of(NUMERALS_SEQ)["text"]
    assert body_words(mail) == words(text[len(NUMERALS_ECHO):])


# -- the AirPods control arm: no acoustic path, nothing of his is cut --------------

# seq -> the line he was quoting (EARS-94 RQ2 False-positive check: each started
# 2.8-20 s into the line and covered at most 57% of it).
QUOTES = {
    148037: "that agap was a one fil bofem ki afole asetuvo",
    166412: "a zun l giba in a uziran tena ilo uni nage as",
    165346: "it lomuso the geki eba aneloku",
    160781: "ipabo etil in your ebik gusilozad",
    152937: "one lekukoso for you",           # cut TODAY: 'Oh, ledo' is all that is mailed
    186568: "defu is the marakup s ezu udu so the",
    169497: "uvav azorepi the efib me tegak it oli",
    169983: "bosav it and ebavun it on",      # cut TODAY: his 'bosav it and ebavun it on' lost
}


@pytest.mark.parametrize("seq", sorted(AIRPODS))
def test_airpods_a_quote_of_a_line_is_his_and_is_not_cut(seq):
    turn = turn_of(seq)
    assert turn["device"] == "airpods" and QUOTES[seq] in " ".join(words(turn["text"]))
    assert all(r["device"] == "airpods" for r in rows_of(seq) if r.get("kind") in ("saying", "said"))
    mail = device_mail(seq)
    assert mail["body"] == turn["text"] + "\n"
    assert "Ears-Echo" not in {k for k, v in mail["headers"].items() if v}


# -- fix-001's guards: each one alone keeps his words (foreman ruling 3) -----------

def _line(sec, utt, text, device="MacBook Pro Speakers", played=None):
    s = "2030-06-09T12:00:%06.3f+10:00"
    return [{"ts": s % sec, "seq": int(sec * 10), "kind": "saying", "agent": "vera", "utt": utt,
             "device": device, "text": text},
            {"ts": s % (sec + (played or 3.0)), "seq": int(sec * 10) + 1, "kind": "said",
             "agent": "vera", "utt": utt, "device": device, "played_s": played or 3.0,
             "cut": False, "text_played_est": text}]


def _turn(sec, t0, t1, text, seq=900, heartbeat=True):
    s = "2030-06-09T12:00:%06.3f+10:00"
    hb = [{"ts": s % 1.0, "seq": 5, "kind": "event", "event": "heartbeat", "source": "mic",
           "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 1.0}]
    return (hb if heartbeat else []), {
        "ts": s % sec, "seq": seq, "kind": "turn", "source": "mic",
        "device": "MacBook Pro Microphone", "session": "ears-keeper", "t0": t0, "t1": t1,
        "text": text}


GUARD_LINE = "The build is green and the branch is pushed to the server for review."


def _only_mail(rows):
    mails = run(rows, DEVICE_RULES, to=None)
    assert len(mails) == 1
    return mails[0]["mail"]


def test_coverage_guard_a_few_of_a_lines_words_are_his():
    """Under COVER of the line is no echo, even on the speakers and inside its play span:
    'it not in the library' is 5 of the line's 28 words."""
    lib_line = ("It's not in the library: the file sits in a folder the indexer skips. "
            "I've put it back up on the screen, paused at the start.")
    hb, t = _turn(14.0, 9.0, 13.0, "Hey Ivy, is it not in the library?")
    mail = _only_mail(hb + _line(2.0, "c0ffee000002", lib_line, played=8.0) + [t])
    assert mail["body"] == t["text"] + "\n" and "Ears-Echo" not in mail["headers"]


def test_open_guard_a_turn_opened_after_the_line_began_quotes_it():
    """On the real span, a turn that opened more than OPEN_S after the line began, and
    is not mostly the line, is him quoting it: the mic heard the line from its first word."""
    text = ("Wait, you said the build is green and the branch is pushed to the server, "
            "so which branch did you push, and who is reviewing it this afternoon?")
    hb, t = _turn(20.0, 9.0, 19.0, text)          # the line began at 2 s, the turn at 10 s
    mail = _only_mail(hb + _line(2.0, "c0ffee000003", GUARD_LINE, played=9.0) + [t])
    assert mail["body"] == text + "\n" and "Ears-Echo" not in mail["headers"]


def test_headphone_guard_a_line_on_the_airpods_is_never_cut():
    """No acoustic path from headphones to the mic (mouth hold.py BARGE_DEVICES): with no
    stream start known (today's timing, so the open guard is off) a whole quote stays."""
    text = "Okay. " + GUARD_LINE + " Good, then merge it."
    hb, t = _turn(9.0, 2.0, 8.0, text, heartbeat=False)
    rows = _line(2.0, "c0ffee000004", GUARD_LINE, device="airpods") + [t]
    mail = _only_mail(rows)
    assert mail["body"] == text + "\n" and "Ears-Echo" not in mail["headers"]
    speakers = _only_mail(_line(2.0, "c0ffee000004", GUARD_LINE) + [t])   # control: cut
    assert speakers["body"] == "Okay. Good, then merge it.\n"
    assert speakers["headers"]["Ears-Echo"] == "vera/c0ffee000004 14 words cut"


def test_ears_echo_is_one_header_a_comma_list_in_turn_order():
    """Two lines cut from one turn: ONE Ears-Echo, an entry per line, in turn order."""
    mail = device_mail(159388)
    assert mail["headers"]["Ears-Echo"] == ("vera/d3777a4c51fa 12 words cut, "
                                            "vera/5a701f1aae7c 16 words cut")


# -- do-002: the peer review's M1 and M2 (reviews/2030-06-09T0959-fix-001) ---------

def test_a_clipped_echo_never_takes_his_words_with_it():
    """M2: whisper missed the line's opening ("The build is green"), and two of his words
    are also the line's ("the", "is"). The ratio alone would start the span there and cut
    "the branch is fine but"; only the echo goes, from its first solid block."""
    text = "So I think the branch is fine but and the branch is pushed to the server for review."
    hb, t = _turn(16.0, 1.0, 15.0, text)
    mail = _only_mail(hb + _line(10.0, "c0ffee000005", GUARD_LINE, played=4.0) + [t])
    assert mail["body"] == "So I think the branch is fine but\n"
    assert mail["headers"]["Ears-Echo"] == "vera/c0ffee000005 10 words cut"


SECOND_LINE = "I have filed the card on the desk and the foreman has it now."


def _two_lines(text):
    hb, t = _turn(12.0, 1.0, 10.0, text)
    return (hb + _line(0.5, "c0ffee000006", GUARD_LINE, played=4.0)
            + _line(6.0, "c0ffee000007", SECOND_LINE, played=4.0) + [t])


@pytest.mark.parametrize("text", [GUARD_LINE + " " + SECOND_LINE + " Yes.",
                                  "Yes. " + GUARD_LINE + " " + SECOND_LINE])
def test_a_lone_word_before_or_after_two_echoes_is_his(text):
    """M1: one word left beside two cut lines, but not BETWEEN them, is his answer."""
    mail = _only_mail(_two_lines(text))
    assert mail["body"] == "Yes.\n"
    assert mail["headers"]["Ears-Echo"] == ("vera/c0ffee000006 14 words cut, "
                                            "vera/c0ffee000007 14 words cut")


def test_a_lone_word_between_two_echoes_is_the_seam():
    """M1, kept as the old rule had it: one word BETWEEN a leading and a trailing echo is
    the seam misheard ("86%" for "eighty-six", seq 59682), and the turn is not mailed.
    bdb9e63's trim drops "<line> Yes. <line>" as well (prefix + suffix + one word), so a
    real "Yes." there was lost before EARS-98 too; it cannot be told from the seam."""
    assert run(_two_lines(GUARD_LINE + " Yes. " + SECOND_LINE), DEVICE_RULES, to=None) == []


# -- EARS-122: Room.echoes() drops a turn WHOLE before trim_echo() runs ------------
# echoes() had no headphone guard, so a one-word turn whose word sits anywhere in a
# line played on the AirPods was dropped as echo: seq 169079, Sun 2030-06-09,
# "So...", lost on master (EARS-98 findings/regression.md 24, 113-121). The cut path
# has skipped headphone lines since EARS-98 (152937); echoes() now does the same.

SO_LINE = "So the plugin loads now."


def _room():
    import importlib.util
    from tests.test_ears_mail import SCRIPT
    spec = importlib.util.spec_from_file_location("ears_mail", SCRIPT)
    em = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(em)
    return em.Room()


def _echoes_so(device):
    """findings/repro_echoes.py's model of 169079: a line containing "so" played 10 s
    on DEVICE, then the one-word turn "So..." 1 s after it finished."""
    ts = "2030-06-09T08:00:%02d+10:00"
    r = _room()
    r.note({"kind": "saying", "utt": "u1", "ts": ts % 20, "agent": "vera",
            "text": SO_LINE, "device": device})
    r.note({"kind": "said", "utt": "u1", "ts": ts % 30, "agent": "vera",
            "played_s": 10, "device": device})
    return r.echoes({"kind": "turn", "ts": ts % 31, "text": "So..."})


@pytest.mark.parametrize("device", ["AirPods Pro", "airpods", "Studio Headphones"])
def test_echoes_a_one_word_turn_after_a_headphone_line_is_his(device):
    assert _echoes_so(device) is False


@pytest.mark.parametrize("device", ["MacBook Pro Speakers", None])
def test_echoes_the_same_turn_after_a_speakers_line_is_still_echo(device):
    assert _echoes_so(device) is True


def test_a_one_word_headphone_turn_is_mailed_and_on_speakers_is_not():
    """End to end (169079's shape): "So..." 1 s after an AirPods line holding "so" is
    mailed whole; after the same line on the speakers it is dropped as echo."""
    hb, t = _turn(6.0, 4.6, 5.0, "So...", heartbeat=False)
    mail = _only_mail(_line(2.0, "c0ffee000122", SO_LINE, device="airpods") + [t])
    assert mail["body"] == "So...\n" and "Ears-Echo" not in mail["headers"]
    assert run(_line(2.0, "c0ffee000122", SO_LINE) + [t], DEVICE_RULES, to=None) == []
