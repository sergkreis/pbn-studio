# Release process

Public contributions arrive through GitHub issues and pull requests. The maintainer retains a separate private development history; public history starts with a reviewed source snapshot. Do not force-push private historical branches into this repository.

1. Review a branch, execute the relevant checks and record exact commit and limitations.
2. Merge the reviewed commit. Use immutable version tags; never replace existing release bytes.
3. Rebuild native worker and Python package from the exact tagged tree.
4. Verify redistribution terms and corresponding source/notices before distributing dependencies.
5. Unpack the archive independently, run native/project/export smoke, calculate SHA256.
6. Publish a draft prerelease, download and hash each uploaded asset, then publish explicitly as prerelease.
7. Stable requires visual and physical acceptance; test counts alone are insufficient.

The initial public snapshot retains the application's internal 0.2.1rc2 version for provenance. It is not a repackaged or reissued private RC2 binary release. Public-only build/documentation changes will be versioned at the next actual release.
