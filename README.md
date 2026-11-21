# acme.transfer — `copy_between`

Copie un **fichier ou un répertoire d'un serveur géré vers un autre**, en **une seule tâche Ansible**, en streamant à travers le contrôleur.

## Pourquoi

`fetch` + `copy` classiques : les données sont écrites sur disque côté contrôleur, temps total = somme des deux transferts. Ici : **pipe en RAM sur le contrôleur** (`ssh src cat | ssh dst cat`), donc temps ≈ max(débit source→contrôleur, débit contrôleur→destination) et rien n'est écrit sur le contrôleur — un simple flux en mémoire.

## Usage

```yaml
- name: Copier un fichier entre deux serveurs
  acme.transfer.copy_between:
    src_host: serveur_a
    src: /srv/app/dump.tgz
    dest_host: serveur_b
    dest: /opt/app/dump.tgz
    mode: "0640"
    owner: app
    group: app
    backup: true
    checksum: true
```

- `src_host`/`dest_host` : noms d'hôte **d'inventaire** (optionnels — par défaut l'hôte de la tâche). Les `ansible_host`, `ansible_user`, `ansible_port`, `ansible_ssh_private_key_file`, `ansible_ssh_common_args` de chaque hôte sont utilisés tels quels, rien à dupliquer.
- `mode`, `owner`, `group`, `backup`, `checksum` : mêmes vocabulaire et sémantique que `ansible.builtin.copy`. Le `become: true` de la tâche s'applique aux commandes de la **destination** (la source est toujours lue comme l'utilisateur SSH, comme pour `fetch`).
- Écriture sur fichier temporaire + `mv` atomique : jamais de fichier partiel à l'emplacement final. `backup` garde l'ancien sous `<dest>.cbw_bak`.
- Écraser un **répertoire** existant exige `backup: true` (sécurité) ; un fichier est écrasé comme le fait `copy`.

## Installation

Déposer ce dépôt sous un chemin de collections :

```bash
git clone <repo> ~/collections/ansible_collections/acme/transfer
export ANSIBLE_COLLECTIONS_PATH=~/collections   # ou ansible.cfg
```

Aucune dépendance : OpenSSH + sh sur le contrôleur, `sh`/coreutils (`cat`, `tar`, `sha256sum`/`shasum`, `stat`, `find`, `du`) sur les cibles.

## Contraintes & sémantique (à lire)

- **Authentification par clés SSH uniquement** (contrôleur → source et contrôleur → destination) : l'exécution est en `BatchMode`, un mot de passe casserait le flux. La présence d'`ansible_password` dans l'inventaire est rejetée avec une erreur explicite.
- Idempotence alignée sur `copy` :
  - contenu identique + aucun attribut demandé → `changed=False`, aucun transfert ;
  - contenu identique + `mode`/`owner`/`group` → les attributs sont comparés (`stat`) puis corrigés si besoin ;
  - contenu différent → transfert streamé puis vérification `sha256`.
- `mode` d'un répertoire : appliqué récursivement (`chmod -R`, comme créait `copy`) ; sinon le `tar` préserve les droits source. `owner`/`group` : `chown -R`.
- `become` sans mot de passe uniquement (`sudo -n` / `su` / `doas`, méthodes et `become_user` de la tâche respectés).
- Répertoires : intégrité vérifiée par nombre de fichiers + volume ; pas de reprise sur interruption (rejouer la tâche).
- Types de connexion supportés dans l'inventaire : `ssh` et `local`.

## Tests

```bash
cd tests
ansible-playbook -i inventory.ini smoke.yml
```

Intégration end-to-end sur des hôtes `local` (aucun SSH requis) : copie fichier + attribus, idempotence, check mode, refus d'écrasement de répertoire, backup, arbre imbriqué.
