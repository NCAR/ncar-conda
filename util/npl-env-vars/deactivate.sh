# NPL: undo activate.d/npl-env-vars.sh. Restore each variable listed in
# $CONDA_PREFIX/etc/npl/env_vars from NPL_SAVED_<CONDA_SHLVL>_<KEY>, or unset it if it had no prior value.
_npl_f="${CONDA_PREFIX}/etc/npl/env_vars"
if [ -r "$_npl_f" ]; then
    while IFS= read -r _npl_kv || [ -n "$_npl_kv" ]; do
        case "$_npl_kv" in *=*) ;; *) continue ;; esac
        _npl_k="${_npl_kv%%=*}"
        case "$_npl_k" in ''|[0-9]*|*[!A-Za-z0-9_]*) continue ;; esac
        if eval "[ -n \"\${NPL_SAVED_${CONDA_SHLVL:-0}_${_npl_k}+x}\" ]"; then
            eval "export ${_npl_k}=\"\${NPL_SAVED_${CONDA_SHLVL:-0}_${_npl_k}}\""
            unset "NPL_SAVED_${CONDA_SHLVL:-0}_${_npl_k}"
        else
            unset "${_npl_k}"
        fi
    done < "$_npl_f"
fi
unset _npl_f _npl_kv _npl_k
