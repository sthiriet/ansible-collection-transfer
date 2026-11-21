# -*- coding: utf-8 -*-
#
# Action plugin : copie de fichiers / répertoires entre deux serveurs gérés,
# en une seule tâche, en streamant à travers le contrôleur (aucun fichier
# tampon sur le contrôleur, juste un pipe en RAM).
#
# Côté cibles : rien à installer (sh, cat/tar, coreutils suffisent).

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import shlex
import subprocess
import threading

from ansible.errors import AnsibleActionFail
from ansible.module_utils.common.text.converters import to_text
from ansible.plugins.action import ActionBase

_STREAM_CHUNK = 256 * 1024


def _q(value):
    return shlex.quote(str(value))


class ActionModule(ActionBase):

    TRANSFERS_FILES = False

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = {}

        result = super(ActionModule, self).run(tmp, task_vars)
        del tmp

        args = self._task.args
        src = args.get('src')
        dest = args.get('dest')
        src_name = args.get('src_host') or str(task_vars.get('inventory_hostname', ''))
        dest_name = args.get('dest_host') or src_name

        if not src or not dest:
            raise AnsibleActionFail("'src' et 'dest' sont obligatoires (chemins sur les machines source/destination)")

        mode = args.get('mode')
        owner = args.get('owner')
        group = args.get('group')
        backup = self._boolean(args.get('backup'), 'backup', False)
        checksum = self._boolean(args.get('checksum'), 'checksum', True)

        src_spec = self._host_spec(src_name)
        dest_spec = self._host_spec(dest_name)
        if src == dest and src_name == dest_name:
            raise AnsibleActionFail("src et dest sont identiques sur le même hôte")

        src_kind = self._detect(src_spec, src)
        if src_kind == 'MISSING':
            raise AnsibleActionFail("fichier/répertoire source introuvable : %s sur %s" % (src, src_spec['name']))

        handler = self._copy_tree if src_kind == 'DIR' else self._copy_file
        return handler(result, src_spec, dest_spec, src, dest, mode, owner, group, backup, checksum)

    def _boolean(self, value, name, default):
        if value is None:
            return default
        from ansible.module_utils.parsing.convert_bool import boolean
        try:
            return boolean(value)
        except TypeError:
            raise AnsibleActionFail("paramètre %s invalide : %r" % (name, value))

    # ------------------------------------------------------------------ hôtes

    def _host_vars(self, name):
        try:
            host = self._task._variable_manager._inventory.get_host(str(name))
        except Exception:
            host = None
        if host is None:
            return {}
        try:
            return dict(host.get_vars())
        except Exception:
            return {}

    def _host_spec(self, name):
        hv = self._host_vars(name)
        if not hv:
            raise AnsibleActionFail("hôte %r introuvable dans l'inventaire" % str(name))

        conn = hv.get('ansible_connection') or 'ssh'
        if conn == 'smart':
            conn = 'ssh'
        if conn == 'local':
            return {'type': 'local', 'name': str(name)}
        if conn != 'ssh':
            raise AnsibleActionFail(
                "type de connexion %r non supporté par copy_between : il faut 'ssh' (ou 'local')" % conn)

        if hv.get('ansible_password') or hv.get('ansible_ssh_pass'):
            raise AnsibleActionFail(
                "authentification par mot de passe non supportée par copy_between : utiliser des clés SSH")

        return {
            'type': 'ssh',
            'name': str(name),
            'addr': str(hv.get('ansible_host') or hv.get('ansible_ssh_host') or name),
            'user': hv.get('ansible_user') or hv.get('ansible_ssh_user') or None,
            'port': hv.get('ansible_port') or hv.get('ansible_ssh_port'),
            'key': hv.get('ansible_ssh_private_key_file') or None,
            'extra': ' '.join([hv.get('ansible_ssh_common_args') or '',
                               hv.get('ansible_ssh_extra_args') or '']),
        }

    def _argv(self, spec, script):
        if spec['type'] == 'local':
            return ['/bin/sh', '-c', script]
        cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15']
        if spec.get('port'):
            cmd += ['-p', str(spec['port'])]
        if spec.get('key'):
            cmd += ['-i', str(spec['key'])]
        cmd += shlex.split(spec.get('extra') or '')
        target = (spec['user'] + '@') if spec.get('user') else ''
        cmd += [target + spec['addr'], script]
        return cmd

    def _run(self, cmd, timeout=3600):
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
        return proc.returncode, proc.stdout, to_text(proc.stderr or b'', errors='replace')

    def _become_wrap(self, script):
        """Le become de la tâche s'applique aux commandes de la destination,
        comme le fait le module copy."""
        pc = self._play_context
        if not pc.become:
            return script
        method = pc.become_method or 'sudo'
        user = pc.become_user
        quoted = _q(script)
        if method == 'sudo':
            return 'sudo -n%s sh -c %s' % ((' -u %s' % _q(user)) if user else '', quoted)
        if method == 'su':
            return 'su - %s -c %s' % (_q(user or 'root'), quoted)
        if method == 'doas':
            return 'doas -n%s sh -c %s' % ((' -u %s' % _q(user)) if user else '', quoted)
        raise AnsibleActionFail("become_method %r non supporté par copy_between (sudo, su, doas)" % method)

    # --------------------------------------------------------- primitives distantes

    def _detect(self, spec, path):
        script = ("if [ -d %s ]; then echo DIR; elif [ -f %s ]; then echo FILE; else echo MISSING; fi"
                  % (_q(path), _q(path)))
        rc, out, err = self._run(self._argv(spec, script))
        value = out.decode('utf-8', 'replace').strip()
        if rc != 0 or value not in ('DIR', 'FILE', 'MISSING'):
            raise AnsibleActionFail("impossible d'accéder à %s sur %s : %s" % (path, spec['name'], err.strip() or value))
        return value

    def _file_checksum(self, spec, path):
        q = _q(path)
        script = "(sha256sum %s 2>/dev/null || shasum -a 256 %s 2>/dev/null) | cut -d' ' -f1" % (q, q)
        rc, out, err = self._run(self._argv(spec, script))
        value = out.decode('utf-8', 'replace').strip().splitlines()[0] if out else ''
        if rc != 0 or len(value) != 64:
            raise AnsibleActionFail("checksum impossible sur %s (%s) : %s" % (path, spec['name'], err.strip()))
        return value

    def _dir_metrics(self, spec, path):
        q = _q(path)
        script = ('N=$(find %s -type f 2>/dev/null | wc -l); '
                  'S=$(du -sB1 %s 2>/dev/null | cut -f1); '
                  'if [ -z "$S" ]; then S=$(( $(du -sk %s 2>/dev/null | cut -f1) * 1024 )); fi; '
                  'echo "$N"; echo "$S"' % (q, q, q))
        rc, out, err = self._run(self._argv(spec, script))
        lines = out.decode('utf-8', 'replace').split()
        if rc != 0 or len(lines) < 2:
            return None
        return (int(lines[0] or 0), int(lines[1] or 0))

    def _attr_script(self, path, mode, owner, group):
        """Vérifie puis applique les attributs (comme file/copy) ; renvoie 0
        avec OK si rien à faire, ou CHANGED si quelque chose a dû être corrigé."""
        q = _q(path)
        body = 'chg=0; '
        if mode:
            body += ('cur=$(stat -c %%a %s 2>/dev/null); '
                     'want=%s; '
                     'if [ "$cur" != "$want" ]; then chmod %s %s || exit 1; chg=1; fi; '
                     % (q, _q(self._norm_mode(mode)), _q(mode), q))
        if owner or group:
            who = '%s:%s' % (owner or '', group or '')
            body += ('cur=$(stat -c %s %s 2>/dev/null); '
                     'want=%s; '
                     'if [ "$cur" != "$want" ]; then chown %s %s || exit 1; chg=1; fi; '
                     % (_q('%U:%G'), q, _q(who), _q(who), q))
        body += 'if [ "$chg" = "0" ]; then echo OK; else echo CHANGED; fi'
        return self._become_wrap(body)

    @staticmethod
    def _norm_mode(mode):
        try:
            return '%o' % int(str(mode), 8)
        except ValueError:
            return str(mode)  # mode symbolique : comparaison approximative, chmod gagne toujours

    # ------------------------------------------------------------------ copies

    def _copy_file(self, result, src_spec, dest_spec, src, dest,
                   mode, owner, group, backup, checksum):
        dest_parent = os.path.dirname(dest.rstrip('/')) or '/'
        tmp = os.path.join(dest_parent, '.' + os.path.basename(dest) + '.cbw_tmp')
        want_attrs = bool(mode or owner or group)

        src_sha = self._file_checksum(src_spec, src)
        dest_kind = self._detect(dest_spec, dest)
        dest_sha = self._file_checksum(dest_spec, dest) if dest_kind == 'FILE' else None

        if self._task.check_mode:
            result['changed'] = (dest_sha != src_sha)
            result['msg'] = 'check mode : aucun transfert effectué'
            result['src_checksum'] = src_sha
            return result

        same = (dest_sha == src_sha)
        if same and not want_attrs and not backup:
            result['changed'] = False
            result['msg'] = 'contenu identique, rien à faire'
            result['src_checksum'] = src_sha
            return result

        if same and not backup:
            rc, out, err = self._run(self._argv(dest_spec, self._attr_script(dest, mode, owner, group)))
            if rc != 0:
                raise AnsibleActionFail("application des attributs impossible sur %s (%s) : %s" %
                                        (dest, dest_spec['name'], err.strip()))
            result['changed'] = out.strip() == b'CHANGED'
            result['src_checksum'] = src_sha
            return result

        verify = ''
        if checksum:
            verify = ("got=$( (sha256sum %s 2>/dev/null || shasum -a 256 %s) | cut -d' ' -f1 ); "
                      'if [ "$got" != %s ]; then rm -f %s; echo "CBW_ERR checksum mismatch" >&2; exit 1; fi; '
                      % (_q(tmp), _q(tmp), _q(src_sha), _q(tmp)))
        attrs = ''
        if mode:
            attrs += ('cur=$(stat -c %%a %s 2>/dev/null); if [ "$cur" != %s ]; then chmod %s %s || exit 1; fi; '
                      % (_q(tmp), _q(self._norm_mode(mode)), _q(mode), _q(tmp)))
        if owner or group:
            who = '%s:%s' % (owner or '', group or '')
            attrs += ('cur=$(stat -c %s %s 2>/dev/null); want=%s; if [ "$cur" != "$want" ]; then chown %s %s || exit 1; fi; '
                      % (_q('%U:%G'), _q(tmp), _q(who), _q(who), _q(tmp)))
        backup_cmd = 'if [ -e %s ]; then cp -a %s %s || exit 1; fi; ' % (
            _q(dest), _q(dest), _q(self._backup_path(dest)))

        script = ('mkdir -p %s; cat > %s || exit 1; %s%s%smv -f %s %s || exit 1; echo OK'
                  % (_q(dest_parent), _q(tmp), verify, attrs, backup_cmd, _q(tmp), _q(dest)))

        rc_src, rc_dst, err_src, err_dst = self._stream(
            self._argv(src_spec, 'cat %s' % _q(src)),
            self._argv(dest_spec, self._become_wrap(script)))
        if rc_src != 0 or rc_dst != 0:
            self._cleanup(dest_spec, tmp)
            raise AnsibleActionFail(self._transfer_error(src_spec, dest_spec, rc_src, rc_dst, err_src, err_dst))

        result['changed'] = True
        result['msg'] = 'fichier %s copié vers %s@%s:%s via le contrôleur' % (src, dest_spec['name'], dest_spec.get('addr', ''), dest)
        result['src_checksum'] = src_sha
        result['dest'] = dest
        return result

    def _copy_tree(self, result, src_spec, dest_spec, src, dest,
                   mode, owner, group, backup, checksum):
        src = src.rstrip('/')
        dest = dest.rstrip('/')
        src_parent, src_base = os.path.split(src)
        dest_parent = os.path.dirname(dest) or '/'
        tmpdir = os.path.join(dest_parent, '.cbw_tree_%s' % os.getpid())
        want_attrs = bool(mode or owner or group)

        src_metrics = self._dir_metrics(src_spec, src)
        dest_exists = self._detect(dest_spec, dest) in ('DIR', 'FILE')
        dest_metrics = self._dir_metrics(dest_spec, dest) if dest_exists else None

        if self._task.check_mode:
            result['changed'] = (not dest_exists or src_metrics != dest_metrics)
            result['msg'] = 'check mode : aucun transfert effectué'
            return result

        same = (dest_exists and src_metrics is not None and src_metrics == dest_metrics)
        if same and not want_attrs and not backup:
            result['changed'] = False
            result['msg'] = 'répertoire identique (même nb de fichiers et volume), rien à faire'
            return result
        if dest_exists and not backup:
            raise AnsibleActionFail(
                "la destination %s existe déjà sur %s : backup: true (l'ancien sera déplacé en %s) ou suppression requise"
                % (dest, dest_spec['name'], self._backup_path(dest)))

        attrs = ''
        if mode:
            attrs += ('cur=$(stat -c %%a "$INNER" 2>/dev/null); if [ "$cur" != %s ]; then chmod -R %s "$INNER" || exit 1; fi; '
                      % (_q(self._norm_mode(mode)), _q(mode)))
        if owner or group:
            who = '%s:%s' % (owner or '', group or '')
            attrs += 'chown -R %s "$INNER" || exit 1; ' % _q(who)

        script = ('mkdir -p %s && mkdir %s || exit 1; '
                  'tar -C %s -xf - || exit 1; '
                  'INNER=$(find %s -mindepth 1 -maxdepth 1 | head -n 1); '
                  'if [ -z "$INNER" ]; then echo "CBW_ERR tar vide" >&2; exit 1; fi; '
                  '%s'
                  'if [ -e %s ]; then mv %s %s || exit 1; fi; '
                  'mv "$INNER" %s || exit 1; '
                  'rmdir %s || exit 1; echo OK'
                  % (_q(dest_parent), _q(tmpdir),
                     _q(tmpdir),
                     _q(tmpdir),
                     attrs,
                     _q(dest), _q(dest), _q(self._backup_path(dest)),
                     _q(dest),
                     _q(tmpdir)))

        rc_src, rc_dst, err_src, err_dst = self._stream(
            self._argv(src_spec, 'tar -C %s -cf - %s' % (_q(src_parent), _q(src_base))),
            self._argv(dest_spec, self._become_wrap(script)))
        if rc_src != 0 or rc_dst != 0:
            self._cleanup(dest_spec, tmpdir)
            raise AnsibleActionFail(self._transfer_error(src_spec, dest_spec, rc_src, rc_dst, err_src, err_dst))

        if checksum:
            final_metrics = self._dir_metrics(dest_spec, dest)
            if src_metrics is not None and final_metrics is not None and src_metrics != final_metrics:
                self._cleanup(dest_spec, dest)
                raise AnsibleActionFail(
                    "vérification du répertoire en échec : %s fichiers / %s octets attendus, %s fichiers / %s octets sur %s"
                    % (src_metrics[0], src_metrics[1], final_metrics[0], final_metrics[1], dest_spec['name']))

        result['changed'] = True
        result['msg'] = 'répertoire %s copié vers %s@%s:%s via le contrôleur' % (src, dest_spec['name'], dest_spec.get('addr', ''), dest)
        result['dest'] = dest
        return result

    def _backup_path(self, dest):
        return '%s.cbw_bak' % dest

    def _cleanup(self, dest_spec, path):
        try:
            self._run(self._argv(dest_spec, 'rm -rf %s; exit 0' % _q(path)), timeout=60)
        except Exception:
            pass

    def _transfer_error(self, src_spec, dest_spec, rc_src, rc_dst, err_src, err_dst):
        parts = []
        if rc_src != 0:
            parts.append('flux source (%s) en erreur rc=%d : %s' % (src_spec['name'], rc_src, err_src.strip()))
        if rc_dst != 0:
            parts.append('traitement destination (%s) en erreur rc=%d : %s' % (dest_spec['name'], rc_dst, err_dst.strip()))
        if not parts:
            parts.append('échec du transfert')
        msg = ' | '.join(parts)
        if rc_dst != 0 and (err_src or err_dst):
            msg += " (si chown/chmod est en cause, relancer avec become: true)"
        return msg

    # ------------------------------------------------------------------ flux

    def _stream(self, src_argv, dst_argv):
        """Épingle stdout(source) sur stdin(destination) : rien ne passe sur
        disque côté contrôleur."""
        src_proc = subprocess.Popen(src_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        dst_proc = subprocess.Popen(dst_argv, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        err_src, err_dst = [], []

        def drain(stream, buf):
            try:
                for line in iter(stream.readline, b''):
                    buf.append(to_text(line, errors='replace'))
            except Exception:
                pass
            finally:
                stream.close()

        t_src = threading.Thread(target=drain, args=(src_proc.stderr, err_src))
        t_dst = threading.Thread(target=drain, args=(dst_proc.stderr, err_dst))
        t_src.start()
        t_dst.start()

        stream_error = None
        try:
            while True:
                chunk = src_proc.stdout.read(_STREAM_CHUNK)
                if not chunk:
                    break
                dst_proc.stdin.write(chunk)
        except (BrokenPipeError, OSError) as exc:
            stream_error = str(exc)
        finally:
            for stream in (dst_proc.stdin, src_proc.stdout):
                try:
                    stream.close()
                except Exception:
                    pass

        rc_src = src_proc.wait()
        rc_dst = dst_proc.wait()
        t_src.join(timeout=10)
        t_dst.join(timeout=10)

        if stream_error and rc_dst == 0:
            rc_dst = 1
            err_dst.append('erreur de flux côté contrôleur : %s\n' % stream_error)

        return rc_src, rc_dst, '\n'.join(err_src), '\n'.join(err_dst)
