# Driftsättning på Oracle Cloud (Always Free)

Resultat: appen körs dygnet runt på en egen liten server, hämtar data två
gånger per dag, skickar notiser och nås på `https://<din-ip>.sslip.io` med
lösenord. Tar ungefär 20–30 minuter första gången.

Menynamnen i Oracles konsol ändras ibland – leta efter motsvarande om något
heter lite annorlunda.

## 1. Skapa servern

1. Logga in på [cloud.oracle.com](https://cloud.oracle.com) → menyn (☰) →
   **Compute → Instances → Create instance**.
2. **Name:** `ipo-bevakning`.
3. **Image and shape → Edit:**
   - Image: **Canonical Ubuntu 24.04** (inte "Minimal").
   - Shape: **VM.Standard.E2.1.Micro** (märkt *Always Free-eligible*).
     Ampere A1 (ARM) fungerar också om den finns ledig, men ger ofta
     "Out of capacity".
4. **Networking:** låt standardnätverket vara och se till att
   **Assign a public IPv4 address** är valt.
5. **Add SSH keys:** välj **Generate a key pair for me** och klicka
   **Save private key**. Spara filen säkert – utan den kommer du inte in.
6. **Create.** När servern är *Running*: skriv upp **Public IP address**.

## 2. Öppna port 80 och 443 i Oracles nätverk

Instansens sida → **Primary VNIC → Subnet** (länken) → **Security Lists** →
*Default Security List* → **Add Ingress Rules**:

| Fält | Värde |
|---|---|
| Source CIDR | `0.0.0.0/0` |
| IP Protocol | TCP |
| Destination Port Range | `80,443` |

(Brandväggen *inne i* servern öppnas automatiskt av installationsskriptet.)

## 3. Logga in på servern

Mac/Linux (Terminal) eller Windows (PowerShell):

```bash
chmod 600 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@DIN_PUBLIKA_IP
```

(På Windows hoppar du över `chmod`; skriv hela filnamnet om `*` inte fungerar.)

## 4. Ge servern läsrätt till GitHub-repot

Repot är privat. Skapa en *deploy key* – en nyckel som bara kan läsa just det
här repot:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/ipo_deploy -N "" -C "ipo-bevakning server"
cat ~/.ssh/ipo_deploy.pub
```

Kopiera raden som skrivs ut. På GitHub: repot **ipo-bevakning → Settings →
Deploy keys → Add deploy key**, klistra in, låt **Allow write access** vara
*urkryssad*, spara.

## 5. Hämta koden och installera

```bash
sudo apt-get install -y git
sudo mkdir -p /opt/ipo-bevakning && sudo chown ubuntu: /opt/ipo-bevakning
git clone -c core.sshCommand="ssh -i $HOME/.ssh/ipo_deploy -o StrictHostKeyChecking=accept-new" \
  git@github.com:davtho80/ipo-bevakning.git /opt/ipo-bevakning
cd /opt/ipo-bevakning && sudo deploy/install.sh
```

Skriptet frågar efter:

- **lösenord** till webbsidan (minst 10 tecken; användarnamnet är `ipo`),
- **ntfy-ämne** – tryck Enter för ett slumpat, säkert ämne.

Det installerar allt som behövs (Python, pdftotext, Caddy för HTTPS), öppnar
brandväggen, startar appen som en tjänst, gör första hämtningen och skriver ut
adressen och ntfy-ämnet.

## 6. Koppla telefonen

1. Installera appen **ntfy** och prenumerera på ämnet som skriptet skrev ut.
2. Öppna adressen (`https://…sslip.io`) och logga in med `ipo` och ditt lösenord.
   Första gången kan HTTPS-certifikatet ta någon minut.
3. Tryck **Skicka testnotis** längst ned på sidan.

## Underhåll

| Vad | Kommando (på servern) |
|---|---|
| Uppdatera till senaste versionen från GitHub | `cd /opt/ipo-bevakning && git pull && sudo deploy/install.sh` |
| Se loggar | `journalctl -u ipo-bevakning -f` |
| Starta om | `sudo systemctl restart ipo-bevakning` |
| Ändra inställningar | `sudo nano /opt/ipo-bevakning/.env` och sedan starta om |
| Byta lösenord | `sudo rm /etc/caddy/ipo-auth && sudo deploy/install.sh` |

**Säkerhetskopior** av databasen (dina markeringar) görs varje natt kl. 03:30
och de 14 senaste sparas i `/var/lib/ipo-bevakning/backups`. De ligger på
samma server, så hämta hem en kopia ibland – från din egen dator:

```bash
ssh -i ~/Downloads/ssh-key-*.key ubuntu@DIN_PUBLIKA_IP \
  'sudo sh -c "ls -t /var/lib/ipo-bevakning/backups/*.gz | head -1 | xargs cat"' > ipo-backup.db.gz
```

## Bra att veta

- **Oracle kan stänga inaktiva gratisservrar.** Enligt Oracles regler för
  Always Free kan en instans som har mycket låg belastning under en vecka
  återtas. Den här appen är mycket lätt. Vill du undvika risken kan du
  uppgradera kontot till *Pay As You Go*: Always Free-resurserna förblir
  gratis, men det kräver betalkort – lägg då in en budgetvarning (Billing →
  Budgets) på en låg summa. Annars: hämta hem säkerhetskopior då och då.
- **Om HTTPS-certifikatet inte går att få** (se `journalctl -u caddy`) kan du
  använda en egen domän eller en gratis från DuckDNS: peka den mot serverns IP,
  kör `sudo DOMAIN=dittnamn.duckdns.org deploy/install.sh` och ändra
  `APP_BASE_URL` i `.env`.
