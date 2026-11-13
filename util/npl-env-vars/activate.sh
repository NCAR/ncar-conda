# NPL: set the variables listed in $CONDA_PREFIX/etc/npl/env_vars (KEY=VALUE per line),
# saving each one's prior value as NPL_SAVED_<CONDA_SHLVL>_<KEY> (per nesting level, so `conda activate --stack` works) so deactivate restores it exactly
# (a variable that was unset stays unset). Installed by util/install_env_vars.
# Sourced by conda in bash/zsh/sh; must not depend on the caller's shell options.
_npl_f="${CONDA_PREFIX}/etc/npl/env_vars"
if [ -r "$_npl_f" ]; then
    while IFS= read -r _npl_kv || [ -n "$_npl_kv" ]; do
        case "$_npl_kv" in *=*) ;; *) continue ;; esac     # skips blanks too
        _npl_k="${_npl_kv%%=*}"
        case "$_npl_k" in ''|[0-9]*|*[!A-Za-z0-9_]*) continue ;; esac   # comments, bad names
        _npl_v="${_npl_kv#*=}"
        if eval "[ -n \"\${${_npl_k}+x}\" ]"; then
            eval "export NPL_SAVED_${CONDA_SHLVL:-0}_${_npl_k}=\"\${${_npl_k}}\""
        fi
        export "${_npl_k}=${_npl_v}"
    done < "$_npl_f"
fi
unset _npl_f _npl_kv _npl_k _npl_v
