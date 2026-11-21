#!/usr/bin/python
# -*- coding: utf-8 -*-
#
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r'''
---
module: copy_between
short_description: Copie un fichier ou un répertoire d'un serveur à un autre, en une tâche, via le contrôleur
description:
  - Copie un fichier ou un répertoire depuis un serveur géré vers un autre
    serveur géré, en une seule tâche.
  - Les données sont streamées à travers le contrôleur (pipe en RAM) :
    aucun fichier temporaire n'est écrit sur le contrôleur, le débit limite
    devient max(débit source->contrôleur, débit contrôleur->destination)
    au lieu de la somme des deux.
  - Aucun paquet à installer sur les cibles (sh, coreutils, sshd suffisent).
  - Même vocabulaire que M(ansible.builtin.copy) pour mode/owner/group/backup,
    et le C(become) de la tâche s'applique aux commandes exécutées sur la
    destination.
  - Authentification par clés SSH uniquement (les variables d'inventaire
    ansible_user, ansible_port, ansible_ssh_private_key_file,
    ansible_ssh_common_args sont lues directement depuis l'inventaire).
options:
  src:
    description: Chemin du fichier ou du répertoire sur la machine source.
    type: str
    required: true
  src_host:
    description:
      - Nom d'hôte d'inventaire de la source.
      - Par défaut, le host sur lequel la tâche s'exécute.
    type: str
  dest:
    description: Chemin de destination sur la machine cible.
    type: str
    required: true
  dest_host:
    description:
      - Nom d'hôte d'inventaire de la destination.
      - Par défaut, le host sur lequel la tâche s'exécute.
    type: str
  mode:
    description: Droits de la destination, même syntaxe que M(ansible.builtin.copy).
    type: str
  owner:
    description: Propriétaire de la destination (nécessite les droits, cf. become).
    type: str
  group:
    description: Groupe de la destination (nécessite les droits, cf. become).
    type: str
  backup:
    description:
      - Garde l'ancienne destination sous C(<dest>.cbw_bak) à défaut de la
        sémantique ~1~ de M(ansible.builtin.copy).
      - Obligatoire pour écraser un répertoire existant.
    type: bool
    default: false
  checksum:
    description: Vérifie l'intégrité après transfert (sha256 pour un fichier,
      nb de fichiers + volume pour un répertoire).
    type: bool
    default: true
notes:
  - become sans mot de passe uniquement (sudo -n / su / doas) : la connexion
    ssh est non interactive.
  - Pour les répertoires, les droits du tar sont préservés pour le \'mode\',
    appliqué récursivement s'il est précisé ; owner/group s'appliquent à
    l'arbre entier (sémantique chown -R de M(ansible.builtin.copy)).
seealso:
  - module: ansible.builtin.copy
  - module: ansible.builtin.fetch
author:
  - "acme.transfer"
'''

EXAMPLES = r'''
- name: Copier un fichier de serveur_a vers serveur_b
  acme.transfer.copy_between:
    src_host: serveur_a
    src: /srv/app/dump.tgz
    dest_host: serveur_b
    dest: /opt/app/dump.tgz
    mode: "0640"
    owner: app
    group: app

- name: Copier un arbre de fichiers, avec backup de l'ancien
  acme.transfer.copy_between:
    src_host: preprod
    src: /var/www/mon_app
    dest_host: prod
    dest: /var/www/mon_app
    backup: true

- name: Directement sur la tâche (src_host / dest_host inutiles)
  hosts: serveur_a
  tasks:
    - acme.transfer.copy_between:
        src: /etc/munin/node.conf
        dest: /etc/munin/node.conf
        dest_host: munin_master
'''

RETURN = r'''
src_checksum:
  description: Empreinte sha256 du fichier source (fichiers uniquement).
  returned: always
  type: str
  sample: "68b329da9893e34099c7d8ad5cb9c940..."
dest:
  description: Chemin écrit sur la destination.
  returned: success
  type: str
msg:
  description: Résumé de l'opération.
  returned: always
  type: str
changed:
  description: True si quelque chose a été modifié ou aurait dû l'être.
  returned: always
  type: bool
'''


def main():
    from ansible.module_utils.basic import AnsibleModule
    module = AnsibleModule(argument_spec={})
    module.fail_json(msg=("copy_between doit s'exécuter via son action plugin : "
                          "vérifier ANSIBLE_COLLECTIONS_PATH ou le chemin 'action_plugins'/collections"))


if __name__ == '__main__':
    main()
