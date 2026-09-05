# Omarchy VRM Battery — plán implementácie

Dátum: 2026-09-05. Stav: implementovaný základ; živé pripojenie čaká na používateľský VRM access token a overenie na konkrétnom GX systéme.
Cieľová platforma: Omarchy 4, prvé overenie na 4.0.2-1.

## 1. Cieľ a rozsah

Vytvoriť verejne použiteľný plugin pre hornú lištu, ktorý sa pripojí k vlastnému VRM účtu používateľa a zobrazí:

- stav nabitia batérie v percentách (SoC),
- aktuálny celkový výkon solárnych panelov,
- aktuálny príkon domácnosti,
- stručný indikátor aktuálnosti údajov.

Príklady kompaktného zobrazenia:

| Stav | Lišta | Detail po kliknutí / tooltip |
| --- | --- | --- |
| Aktuálne | `🔋78% ☀2.4kW ⌂650W ·5s` | Batéria 78 %, solár 2400 W, dom 650 W; údaje potvrdené pred 5 s |
| Bez výroby | `🔋62% ☀0W ⌂580W ·8s` | Potvrdená nulová výroba, aktuálna spotreba |
| Neaktuálne | `🔋62% ☀— ⌂— !2m` | Výkony sú zastarané; posledné hodnoty a ich vek iba v detaile |
| Čiastočné údaje | `🔋78% ☀— ⌂650W ?` | Solárne meranie chýba; vek každého dostupného údaja zvlášť |
| Offline | `🔋— ☀— ⌂— ×` | Spojenie prerušené; posledné známe hodnoty iba v detaile |
| Bez účtu | `🔋 Pripojiť VRM` | Sprievodca pripojením |

Ikony sú ilustračné; použiť ikony a farby aktuálnej Omarchy témy. Stav musí byť čitateľný aj bez farby. Lišta má jeden riadok, malé rozostupy a žiadne slovné popisky pri bežnom stave. Cieľová šírka približne 220–260 logických pixelov pri predvolenom fonte; overiť ju na skutočnej lište a pri škálovaní. Tabulárne číslice a stabilné sloty obmedzia posúvanie susedných widgetov. Pod 1000 W zobrazovať celé W, od 1000 W kW s najviac jedným desatinným miestom; zaokrúhľovanie hranice jednotiek riešiť konzistentne. Detail vždy ukáže celé W. Na úzkej lište znižovať medzery, nie skryť niektoré z troch meraní alebo stav aktuálnosti.

Indikátor `·5s` znamená vek najstaršieho potvrdenia platnosti z troch zobrazených meraní, nie iba vek poslednej MQTT správy. Sekundy zobrazovať do 59 s, potom celé minúty; stačí prekreslenie veku každých 5 s. `!` označuje zastarané údaje, `?` neúplné alebo neoveriteľné údaje a `×` offline. Pri čiastočne zastaraných údajoch potlačiť iba príslušné hodnoty; aktuálne merania zostávajú viditeľné. Zastarané SoC môže zostať uvedené iba s globálnym `!` a vysvetlením v detaile. Nikdy neukazovať chýbajúci alebo zastaraný výkon ako 0 W.

Tooltip a detail vysvetlia všetky ikony a obsahujú názov inštalácie, presné hodnoty, samostatný vek a platnosť každého merania, stav spojenia, nastavenia účtu a tlačidlo otvorenia príslušného VRM dashboardu. Ak zdroj neposkytuje čas fyzického merania, používať označenie „potvrdené pred“, nie predstierať presný vek merania.

Prvá verzia zobrazuje jednu používateľom vybranú inštaláciu, jej systémové SoC, celkovú solárnu výrobu a spotrebu domu. Smer ani výkon nabíjania/vybíjania batérie nie sú súčasťou požadovaného UI ani dátového modelu. Podporuje zmenu účtu a inštalácie. Neobsahuje ovládanie meniča, nabíjania ani ESS, históriu či analytiku. Žiadny identifikátor konkrétneho používateľa ani jeho token nebude predvolenou konfiguráciou.

## 2. Overený základ a podmienky

Omarchy 4 používa Quickshell; používateľské pluginy majú manifest a žijú v `~/.config/omarchy/plugins/<plugin-id>/`. Lokálne sú dostupné príkazy `omarchy plugin add`, `enable`, `disable`, `update`, `remove` a `validate`. Konfigurácia lišty je v `~/.config/omarchy/shell.json`. Presnú schému manifestu a rozhranie služby/widgetu prevziať z nainštalovanej verzie pri implementácii.

VRM poskytuje REST API na `https://vrmapi.victronenergy.com/v2`; autentifikácia používa hlavičku `x-authorization: Token <token>`. Nový kód nemá používať zastaraný Bearer login.

Oficiálna MQTT dokumentácia uvádza TLS, prihlasovanie emailom a heslom `Token <token>`, broker z rozšíreného zoznamu inštalácií a notifikácie `N/<portal-id>/…`. Číselné ID inštalácie nie je MQTT portal ID. Keepalive používa `R/<portal-id>/keepalive`, timeout je 60 sekúnd; po prvom načítaní existuje voľba `suppress-republish`. Oprávnenie Monitor Only povoľuje prijímanie notifikácií. Prístupnosť read/keepalive požiadaviek s týmto oprávnením treba samostatne overiť.

Podmienkou živého režimu je kompatibilné GX zariadenie, dostupné merania a aktivovaná MQTT služba/forwardovanie podľa nastavení Venus OS. Sprievodca musí tieto podmienky diagnostikovať. Samotné prihlásenie do VRM negarantuje živé merania.

## 3. Architektúra

Navrhované ID pluginu: `community.vrm-battery` (pred publikovaním overiť kolízie).

Tok dát:

```text
VRM REST ── výber účtu a inštalácie ──┐
                                    ├─ Python backend ── lokálny IPC ── Quickshell widget
VRM MQTT/TLS ── živé merania ─────────┘        │
                                      systémový keyring
```

- **QML plugin:** služba, widget lišty, detail a sprievodca; sieťové tajomstvá nesmú byť v modeli widgetu.
- **Python backend:** REST discovery, MQTT klient, normalizácia meraní, reconnect a bezpečné uloženie tokenu. Použiť udržiavanú MQTT knižnicu, HTTP klient a explicitný Secret Service backend; konkrétne verzie uzamknúť pri implementácii.
- **Životný cyklus:** backend spúšťa služba pluginu. Jeden backend na používateľskú reláciu, aj pri viacerých monitoroch. Disable, reload a ukončenie shellu musia spojenie korektne zavrieť; žiadne osirelé procesy.
- **IPC:** Unix socket v `$XDG_RUNTIME_DIR/omarchy-vrm-battery/`, adresár 0700, socket 0600, kontrola UID klienta. Iba lokálne štruktúrované správy; žiadny HTTP server. QML komunikáciu sprostredkuje pomocný proces cez stdin/stdout, ak shell nemá vhodné natívne rozhranie.
- **Uloženie:** netajná konfigurácia v XDG config; token iba v keyringu. Posledné meranie štandardne iba v pamäti.

Navrhovaný layout repozitára:

```text
manifest.json
Service.qml
BarWidget.qml
Panel.qml
Setup.qml
backend/vrm_battery/
  __main__.py
  auth.py
  discovery.py
  mqtt_client.py
  measurements.py
  ipc.py
  config.py
pyproject.toml
scripts/
tests/fixtures/
README.md
SECURITY.md
LICENSE
PLAN.md
```

## 4. Bezpečné pripojenie ľubovoľného účtu

1. Používateľ nainštaluje plugin a zvolí „Pripojiť VRM“.
2. Sprievodca otvorí `https://vrm.victronenergy.com/access-tokens` v predvolenom prehliadači. Používateľ sa prihlasuje priamo u Victronu a vytvorí samostatný token pomenovaný napríklad „Omarchy Battery“.
3. Zadá email pre MQTT a token do maskovaného poľa. Plugin nečíta cookies ani heslá z prehliadača. Token neprenáša cez argumenty procesu, URL alebo premenné prostredia.
4. Backend overí token cez dokumentované API, zistí identitu používateľa a načíta jeho dostupné inštalácie. Presný identity endpoint potvrdiť podľa aktuálnej OpenAPI schémy; netvoriť domnelý OAuth flow a nespoliehať sa na dekódovanie tokenu.
5. Používateľ vyberie inštaláciu zo zoznamu. Backend načíta jej portal ID a broker; nedávať používateľovi povinnosť ručne počítať broker.
6. Sprievodca otestuje TLS/MQTT, oprávnenia a merania SoC, solárnej výroby a spotreby domu. Pri chýbajúcom meraní vysvetlí obmedzenie a umožní uložiť čiastočne funkčný profil; nikdy nedoplní vymyslenú nulu. Po overení spojenia ukáže prvé dostupné vzorky.
7. Token uloží cez systémový Secret Service keyring. Ak nie je dostupný alebo odomknutý, ponúkne pripojenie iba na aktuálnu reláciu a návod na nastavenie keyringu. Žiadny automatický plaintext fallback.

Bezpečnostné požiadavky:

- Odporučiť Monitor Only prístup. Overiť, či VRM umožňuje obmedziť konkrétny token; ak nie, vysvetliť, že token dedí práva účtu a samostatný monitorovací účet znižuje rozsah prístupu.
- Plugin odosiela iba explicitne povolené discovery a read/keepalive požiadavky. MQTT `W/` témy sú zakázané na úrovni transportu, nie iba skryté v UI.
- Kontrola TLS certifikátu aj hostname je povinná. Overiť aktuálny trust chain Victronu; prípadný potrebný CA certifikát distribuovať z overeného upstreamu s dokumentovanou aktualizáciou. Nikdy nepoužiť insecure režim.
- Broker musí patriť medzi očakávané Victron endpointy; token neposielať na ľubovoľný server ani cez presmerovanie na iný origin.
- Bez tokenov v logoch, diagnostike, fixture súboroch a chybových hláseniach. Nelogovať celé HTTP odpovede. Užívateľské texty v QML renderovať ako obyčajný text.
- Netajné profily napriek tomu chrániť právami 0600; email a názov inštalácie sú osobné údaje.
- „Odpojiť účet“ zavrie spojenie, odstráni token z keyringu a vymaže lokálny profil. Samostatne ponúkne odkaz na odvolanie tokenu vo VRM; lokálne zmazanie token neodvoláva.
- Rotácia tokenu, odvolaný token, zamknutý keyring a zánik prístupu k inštalácii majú vlastné zrozumiteľné stavy. Žiadna telemetria ani externý server projektu.

## 5. Živé merania a ich význam

REST slúži predovšetkým na discovery. MQTT je primárny transport meraní; 60-sekundový REST polling sa nesmie označiť za real-time.

Pri implementácii najprv overiť oficiálne D-Bus mapovanie a skutočný anonymizovaný payload. SoC vybrať zo systémového battery monitora, nie z náhodnej prvej batérie. Presné MQTT témy, inštancie, jednotky a dostupnosť potvrdiť v etape A.

Solárny výkon musí zodpovedať celkovej aktuálnej PV výrobe vo VRM vrátane podporovaných DC nabíjačov aj AC solárnych meničov. Preferovať overený systémový agregát; ak nie je dostupný, sčítať iba preukázateľne disjunktné zdroje. Nesčítať agregát s jeho zložkami ani celkový výkon meniča opäť s jeho fázami. Rozlišovať neprítomný zdroj od neaktuálneho zdroja: ak súčet závisí od chýbajúcej vzorky, celok označiť neúplný. Potvrdená nočná výroba môže byť 0 W; neprítomná telemetria nie je automaticky nula.

Spotreba domu znamená aktuálnu celkovú spotrebu záťaží sledovaných systémom VRM, nie odber zo siete a nie výkon batérie. Zvoliť overený systémový agregát spotreby všetkých relevantných fáz a vetiev. Ak zapojenie alebo chýbajúce meradlo nepokrýva celý dom, v sprievodcovi aj detaile výslovne označiť rozsah „sledované záťaže“; úplnú spotrebu domu nemožno garantovať pre každé zapojenie. V etape A určiť podporu DC záťaží podľa významu zodpovedajúceho VRM dashboardu.

Navrhnutý dátový kontrakt (rovnaká štruktúra platí pre každé meranie):

```json
{
  "schemaVersion": 1,
  "connection": "live",
  "soc": {"value": 78.2, "unit": "%", "validity": "fresh", "confirmedAt": "2026-09-05T12:00:00Z"},
  "solar": {"value": 2400, "unit": "W", "validity": "fresh", "confirmedAt": "2026-09-05T12:00:01Z"},
  "home": {"value": 650, "unit": "W", "validity": "fresh", "confirmedAt": "2026-09-05T12:00:01Z"},
  "consumptionScope": "monitored-loads",
  "source": "mqtt"
}
```

- Povolené stavy spojenia: unconfigured, connecting, live, stale, offline, auth-required, unsupported, error. Platnosť každej metriky: fresh, stale, missing, incomplete, unverified; samotný transport live neznamená platnosť všetkých meraní.
- SoC musí byť konečné číslo v rozsahu 0–100; null/neplatná hodnota sa nezmení na nulu. Prázdny MQTT payload zneplatní príslušné meranie.
- Výkony interne držať vo W bez zaokrúhľovania. Znamienka a prípadný merací šum normalizovať až podľa overenej sémantiky zdroja; slepé použitie absolútnej hodnoty by mohlo skryť chybu mapovania.
- SoC, solár a dom majú samostatnú platnosť a čas potvrdenia; pri agregáte platnosť aj čas vychádzajú z najstaršej potrebnej zložky. Nová vzorka spotreby nesmie obnoviť platnosť starého solárneho výkonu. Vek počítať monotónnymi hodinami; UTC časy slúžia na diagnostiku.

- Pri pripojení najprv subscribe, potom úvodný snapshot/keepalive. Navrhnutý keepalive interval 30 s; po inicializácii suppress-republish. Úzky odber len potrebných tém a potvrdení, žiadny globálny wildcard.
- Nemenná hodnota nemusí vyvolať notifikáciu. Samotné ticho preto nie je dôkaz neaktuálnosti; ani MQTT PING nedokazuje živé GX. Navrhnúť periodické read/snapshot potvrdenie každých 30 s, overiť jeho oprávnenia a správanie pri odpojenom GX. Čas prijatia neprezentovať ako čas fyzického merania.
- Po 90 s bez potvrdenia čerstvosti označiť údaje stale, po strate transportu ihneď offline. Starý výkon nesmie vyzerať aktuálne. Pri nepodporovanom overení čerstvosti neoznačovať stream za spoľahlivo live.
- Reconnect s exponenciálnym backoffom a jitterom, napríklad 1–60 s; po suspend/resume nový snapshot. Pri 401/403 zastaviť opakovanie a vyžiadať opravu prístupu. REST 429 rešpektuje Retry-After.
- Vykreslenie obmedziť na najviac jednu aktualizáciu za sekundu. Cieľ: doručenú platnú zmenu ukázať do 1 s. Celkové oneskorenie závisí od GX a siete; merať ho a nesľubovať pevnú latenciu z cloudu.

REST fallback je voliteľný neskorší doplnok; ak vznikne, musí zobrazovať „periodické údaje“ a čas zdrojového záznamu. Nenahrádza splnenie živého režimu.

## 6. Inštalácia, distribúcia a kompatibilita

- Distribuovať ako samostatný Git repozitár s manifestom v koreni; po publikovaní zdokumentovať `omarchy plugin add <URL-repozitára> --enable` a postup prvého spustenia.
- Overiť presný inštalačný lifecycle Omarchy a spôsob prípravy Python závislostí. Git clone sám osebe nemusí nainštalovať runtime: poskytnúť explicitný idempotentný setup do používateľského venv mimo Git checkoutu. Nespúšťať pip inštaláciu pri každom načítaní QML ani nemeniť systémový Python.
- Inštalácia bez root práv okrem prípadných používateľom zvolených systémových závislostí. Žiadne zásahy do `/usr/share/omarchy/`.
- Konfiguráciu lišty meniť podporovaným plugin CLI; zachovať existujúce widgety a používateľské rozloženie. Nikdy neresetovať shell konfiguráciu.
- Dokumentácia v angličtine pre širšie použitie; UI pripraviť na preklady a dodať EN/SK. Dokumentovať minimálnu overenú verziu, potrebné GX nastavenia a obmedzenia účtov.
- Verzionovať plugin, IPC schému a konfiguráciu. Aktualizácia nesmie zmazať účet. Downgrade s neznámou schémou má skončiť jasnou chybou.
- Odinštalovanie pokryje backend, runtime aj tajomstvá; overiť, či Omarchy podporuje uninstall hook. Ak nie, pridať zdokumentované „Odpojiť a vyčistiť“ pred `omarchy plugin remove` a manuálny cleanup postup.

## 7. Etapy a akceptačné kritériá

### A — technické overenie, pred tvorbou celého UI

- Preskúmať manifest, service/bar-widget API, lifecycle a inštalátor Omarchy 4.
- Overiť identitu cez PAT, zoznam inštalácií, portal ID a broker.
- S testovacím účtom overiť MQTT token, TLS, Monitor Only a read/keepalive vrátane prípadu, keď nie je otvorený VRM dashboard.
- Overiť SoC, agregáciu AC/DC solárnej výroby a spotreby domu pri rôznych zapojeniach, disconnect GX a samostatné potvrdenie čerstvosti každej metriky.
- **Výstup:** zdokumentovaný overený protokol a anonymizované vzorky. Ak Monitor Only nepodporuje potrebný živý tok, zaznamenať obmedzenie a vyriešiť bezpečný podporovaný postup pred označením funkcie za hotovú; automaticky nezvyšovať oprávnenia.

### B — backend a bezpečné pripojenie

- Implementovať keyring/session-only režim, discovery, výber inštalácie, transport a normalizáciu.
- Dodať reconnect, stavy chýb, IPC a odpojenie/rotáciu tokenu.
- **Hotovo, keď:** dva odlišné testovacie účty fungujú bez úpravy kódu, token neuniká cez argv/logy/disk a nemožno odoslať riadiacu MQTT požiadavku.

### C — plugin v hornej lište

- Implementovať service, widget, detail, setup a podporu tém.
- **Hotovo, keď:** SoC, solárny výkon a spotreba domu sú správne, vek dát viditeľný a widget kompaktný; zvládne chýbajúce aj čiastočne zastarané merania, reload a viac monitorov bez duplikácie spojení.

### D — distribúcia a overenie vydania

- Inštalácia na čistom Omarchy 4 profile, chýbajúci keyring, upgrade, disable a úplné odinštalovanie.
- Testy normalizácie, agregácie bez dvojitého započítania, formátovania W/kW, null payloadov, snapshotov, samostatného starnutia metrík a reconnectu pomocou anonymných fixtures/fake servera.
- Bezpečnostné testy: zlý certifikát/hostname, cudzí broker, redirect, nepovolený IPC klient, neplatný token a zákaz W tém.
- Manuálne porovnať s VRM SoC, solárny výkon a spotrebu domu cez deň aj v noci, pri kombinácii AC/DC PV a viacerých fázach; skúsiť vypnutie GX, siete, zatvorenie dashboardu a uspatie počítača. Nemenné platné údaje nesmú falošne starnúť a odpojené GX nesmie zostať live.
- Spustiť `omarchy plugin validate <plugin-folder>`; skontrolovať čitateľnosť svetlej/tmavej témy a úzkej lišty.
- Zmerať pokojovú spotrebu CPU, pamäť, sieťové prenosy a oneskorenie; overiť, že sa nehromadia procesy ani spojenia.
- **Hotovo, keď:** iný používateľ podľa README bezpečne pripojí svoj účet, vidí overené živé hodnoty a vie token odvolať aj plugin odstrániť.

## 8. Zdroje a otvorené overenia

Overené 2026-09-05:

- VRM API a autentifikácia: https://vrm-api-docs.victronenergy.com/
- Oficiálny MQTT protokol, broker discovery a tokeny: https://github.com/victronenergy/dbus-flashmq
- Správa vlastných tokenov: https://vrm.victronenergy.com/access-tokens
- Lokálny Omarchy 4.0.2-1: `omarchy version`, `omarchy plugin --help`, `/usr/share/omarchy/shell/plugins/README.md`.
- Lokálne pravidlá integrácie: `/home/juraj/.codex/skills/omarchy/plugins.md`.

Konkrétne D-Bus cesty, identity endpoint, rozsah práv PAT, Monitor Only keepalive, dôkaz čerstvosti GX a presný packaging helpera sú zámerne označené ako implementačné overenia. Plán ich nepovažuje za otestované na používateľovom účte. Pri implementácii zapísať zistené verzie a výsledky do repozitára.
