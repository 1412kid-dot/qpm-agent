"""Legacy room-temperature Sellmeier models, wavelengths in nm."""
import math

def refractive_indices(material,wavelength_nm):
    u=wavelength_nm/1000; q=u*u
    if material not in {'LN','LT'}: raise ValueError('unknown material')
    limit=5 if material=='LN' else 4
    if not .4<=u<=limit: raise ValueError(f'{material} Sellmeier domain is 400..{limit*1000} nm')
    if material=='LN':
        no=math.sqrt(1+2.6734*q/(q-.01764)+1.2290*q/(q-.05914)+12.614*q/(q-474.6))
        ne=math.sqrt(1+2.9804*q/(q-.02047)+.5981*q/(q-.0666)+8.9543*q/(q-416.08))
    else:
        no=math.sqrt(4.51224+.0847522/(q-.19876**2)-.0239046*q)
        ne=math.sqrt(4.52999+.0844313/(q-.20344**2)-.0237909*q)
    return no,ne
