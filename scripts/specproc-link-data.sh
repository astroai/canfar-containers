#!/bin/bash
# Point specproc package data paths at one readonly tree.
# Run during the image build, in the same RUN that created the files, so the
# bulky copies never land in a layer. Session users cannot rewrite these
# links; mount the tree at /specproc-data.
#
# One directory is shared wherever two packages read the same bytes:
#   linelists/synspec/  synspec and synple (gfATO, gfMOLsun, gfTiO, H2O .11)
# Other products that share a name but not a file format stay side by side
# (FSPS MIST isochrones vs the isochrones package's MIST tables, raw MARCS
# vs Korg's HDF5 vs pymoog's model pickles).

set -euo pipefail

SPECPROC_DATA="${SPECPROC_DATA:-/specproc-data}"

point_at() {
    local dest="$1" rel="$2" parent
    parent="$(dirname "${dest}")"
    [[ -d "${parent}" ]] || return 0
    rm -rf "${dest}"
    ln -s "${SPECPROC_DATA}/${rel}" "${dest}"
}

# FSPS code and the small data/ index stay in the image. Spectral libraries,
# isochrones, nebular grids, and dust are the mount.
if [[ -d /opt/astroai/fsps ]]; then
    rm -rf /opt/astroai/fsps/.git
    point_at /opt/astroai/fsps/SPECTRA fsps/SPECTRA
    point_at /opt/astroai/fsps/ISOCHRONES fsps/ISOCHRONES
    point_at /opt/astroai/fsps/nebular fsps/nebular
    point_at /opt/astroai/fsps/dust fsps/dust
fi

for repo in \
    /opt/astroai/Turbospectrum_NLTE \
    /opt/astroai/TSFitPy \
    /opt/astroai/ferre \
    /opt/astroai/pykurucz \
    /opt/astroai/iNNterpol
do
    rm -rf "${repo}/.git"
done

# pyKurucz GFALL is a line list. The emulator weights stay in the checkout.
if [[ -d /opt/astroai/pykurucz/lines ]]; then
    point_at /opt/astroai/pykurucz/lines/gfallvac.latest linelists/kurucz/gfallvac.latest
fi

# Korg's built-in lists stay in the Julia package. Atmosphere HDF5 files are
# the mount copy; the artifact directory layout stays so Julia can find them.
korg_art=/opt/astroai/julia/depot/artifacts
if [[ -d "${korg_art}" ]]; then
    while IFS= read -r -d '' grid; do
        base="$(basename "${grid}")"
        [[ "${base}" == ._* ]] && continue
        rm -f "${grid}"
        ln -s "${SPECPROC_DATA}/atmospheres/korg/${base}" "${grid}"
    done < <(find "${korg_art}" \( -type f -o -type l \) -name '*.h5' -print0)
    rm -rf /opt/astroai/julia/depot/compiled /opt/astroai/julia/depot/downloads
fi

sp="$(echo /opt/astroai/venv/specproc/lib/python*/site-packages)"
if [[ -d "${sp}" ]]; then
    # synspec and synple both want the same binary Kurucz/ExoMol lists.
    [[ -d "${sp}/synspec" ]] && point_at "${sp}/synspec/linelists" linelists/synspec
    [[ -d "${sp}/synple" ]] && point_at "${sp}/synple/linelists" linelists/synspec

    [[ -d "${sp}/sedpy/data" ]] && point_at "${sp}/sedpy/data/filters" filters/sedpy
    if [[ -d "${sp}/pyphot/libs" ]]; then
        point_at "${sp}/pyphot/libs/new_filters.hd5" filters/pyphot/new_filters.hd5
        point_at "${sp}/pyphot/libs/synphot_nonhst.hd5" filters/pyphot/synphot_nonhst.hd5
    fi
    if [[ -d "${sp}/astroARIADNE/Datafiles" ]]; then
        point_at "${sp}/astroARIADNE/Datafiles/model_grids" models/ariadne/grids
    fi
fi

# pymoog's MOOG binary stays under .pymoog. Its VALD/Kurucz lists and its
# model grid are different files from synspec's .11 lists and from raw MARCS.
pymoog_files=/opt/astroai/pymoog-home/.pymoog/files
if [[ -d /opt/astroai/pymoog-home/.pymoog ]]; then
    mkdir -p "${pymoog_files}"
    if [[ -e "${pymoog_files}/pymoog_lf" && ! -L "${pymoog_files}/pymoog_lf/linelist" ]]; then
        rm -rf "${pymoog_files}/pymoog_lf"
    fi
    mkdir -p "${pymoog_files}/pymoog_lf"
    point_at "${pymoog_files}/pymoog_lf/linelist" linelists/pymoog
    point_at "${pymoog_files}/pymoog_lf/model" atmospheres/pymoog
    rm -f "${pymoog_files}"/pymoog_lf_*.tar.gz
    rm -rf /opt/astroai/pymoog-home/.sme \
        /opt/astroai/pymoog-home/.astropy \
        /opt/astroai/pymoog-home/.cache
fi

mkdir -p /opt/astroai/share/specproc
# dustmaps expands ${SPECPROC_DATA} when it reads this file.
printf '%s\n' "{\"data_dir\": \"\${SPECPROC_DATA}/dustmaps\"}" \
    > /opt/astroai/share/specproc/dustmapsrc
chmod a+r /opt/astroai/share/specproc/dustmapsrc
