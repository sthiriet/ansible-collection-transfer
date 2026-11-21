# Changelog

## v1.0.0

- Nouveau plugin d'action `acme.transfer.copy_between` : copie d'un fichier ou
  d'un répertoire d'un serveur géré vers un autre, en une seule tâche, données
  streamées à travers le contrôleur (pipe en RAM, aucun fichier tampon).
- Sémantique `mode`/`owner`/`group`/`backup` alignée sur `ansible.builtin.copy`,
  `become` appliqué côté destination, check mode + idempotence.
- Intégrité : `sha256` (fichiers) / volume + nombre de fichiers (répertoires).
- Clés SSH uniquement ; credentials lus dans l'inventaire.
