# NPL: csh/tcsh version of deactivate.sh. Restores each variable listed in
# $CONDA_PREFIX/etc/npl/env_vars from NPL_SAVED_<CONDA_SHLVL>_<KEY>, or unsets it if it had no prior value.
if ( -r "${CONDA_PREFIX}/etc/npl/env_vars" ) then
    set _npl_noglob = $?noglob
    set _npl_lvl = 0
    if ( $?CONDA_SHLVL ) set _npl_lvl = $CONDA_SHLVL
    set noglob
    foreach _npl_kv ( `grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${CONDA_PREFIX}/etc/npl/env_vars"` )
        set _npl_k = `echo "$_npl_kv" | sed 's/=.*//'`
        printenv NPL_SAVED_${_npl_lvl}_$_npl_k >& /dev/null
        if ( $status == 0 ) then
            setenv $_npl_k "`printenv NPL_SAVED_${_npl_lvl}_$_npl_k`"
            unsetenv NPL_SAVED_${_npl_lvl}_$_npl_k
        else
            unsetenv $_npl_k
        endif
    end
    if ( ! $_npl_noglob ) unset noglob
    unset _npl_noglob _npl_lvl _npl_kv _npl_k
endif
