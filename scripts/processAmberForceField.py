#!/usr/bin/env python
import sys
import math
import simtk.openmm.app.element as element
import simtk.unit as unit
import subprocess
import datetime
import string

def fix(atomClass):
    if atomClass == 'X':
        return ''
    return atomClass

elements = {}
for elem in element.Element._elements_by_symbol.values():
    num = elem.atomic_number
    if num not in elements or elem.mass < elements[num].mass:
        elements[num] = elem

OTHER = 0
ATOMS = 1
CONNECT = 2
CONNECTIVITY = 3
RESIDUECONNECT = 4
section = OTHER

charge14scale = 1.0/1.2
epsilon14scale = 0.5

skipResidues = ['CIO', 'IB'] # "Generic" ions defined by Amber, which are identical to other real ions
skipClasses = ['OW', 'HW'] # Skip water atoms, since we define these in separate files

class AmberParser(object):
    def __init__(self):
        self.residueAtoms = {}
        self.residueBonds = {}
        self.residueConnections = {}

        self.types = []
        self.type_names = []
        self.masses = {}
        self.resAtomTypes = {}
        self.vdwEquivalents = {}
        self.vdw = {}
        self.charge = {}
        self.bonds = []
        self.angles = []
        self.torsions = []
        self.impropers = []
        
        self.set_originance()

    def addAtom(self, residue, atomName, atomClass, element, charge, use_numeric_types=True):
        if residue is None:
            return
        type_id = len(self.types)
        self.residueAtoms[residue].append([atomName, type_id])
        self.types.append((atomClass, element, charge))
        if use_numeric_types:
            self.type_names.append("%d" % (type_id))
        else:
            self.type_names.append("%s-%s" % (residue, atomName))

    def addBond(self, residue, atom1, atom2):
        if residue is None:
            return
        self.residueBonds[residue].append((atom1, atom2))

    def addExternalBond(self, residue, atom):
        if residue is None:
            return
        if atom != -1:
            self.residueConnections[residue] += [atom]

    def process_mol2_file(self, inputfile):
        import gafftools  # Late import to delay importing optional modules
        mol2_parser = gafftools.Mol2Parser(inputfile)
        residue_name = mol2_parser.atoms.resName[1]  # To Do: Add check for consistency
        
        self.residueAtoms[residue_name] = []
        self.residueBonds[residue_name] = []
        self.residueConnections[residue_name] = []        
    
        for (i, name, x, y, z, atype, code, resname, charge) in mol2_parser.atoms.itertuples(False):
            full_name = residue_name + "_" + name
            element_symbol = gafftools.gaff_elements[atype]
            e = element.Element.getBySymbol(element_symbol)
            self.addAtom(resname, name, atype, e, charge, use_numeric_types=False)  # use_numeric_types set to false to use string-based atom names, rather than numbers
            self.vdwEquivalents[full_name] = atype

        for (id0, id1, bond_type) in mol2_parser.bonds.itertuples(False):
            i = id0 - 1  # Subtract 1 for zero based indexing in OpenMM???  
            j = id1 - 1  # Subtract 1 for zero based indexing in OpenMM???  
            self.addBond(residue_name, i, j)

    def process_library_file(self, inputfile):
        """Read a library file"""
        for line in open(inputfile):
            if line.startswith('!entry'):
                fields = line.split('.')
                residue = fields[1]
                if residue in skipResidues:
                    residue = None
                    continue
                key = fields[3].split()[0]
                if key == 'atoms':
                    section = ATOMS
                    self.residueAtoms[residue] = []
                    self.residueBonds[residue] = []
                    self.residueConnections[residue] = []
                elif key == 'connect':
                    section = CONNECT
                elif key == 'connectivity':
                    section = CONNECTIVITY
                elif key == 'residueconnect':
                    section = RESIDUECONNECT
                else:
                    section = OTHER
            elif section == ATOMS:
                fields = line.split()
                atomName = fields[0][1:-1]
                atomClass = fields[1][1:-1]
                if fields[6] == '-1':
                    # Workaround for bug in some Amber files.
                    if atomClass[0] == 'C':
                        elem = elements[6]
                    elif atomClass[0] == 'H':
                        elem = elements[1]
                    else:
                        raise ValueError('Illegal atomic number: '+line)
                else:
                    elem = elements[int(fields[6])]
                self.charge = float(fields[7])
                addAtom(residue, atomName, atomClass, elem, charge)
            elif section == CONNECT:
                addExternalBond(residue, int(line)-1)
            elif section == CONNECTIVITY:
                fields = line.split()
                addBond(residue, int(fields[0])-1, int(fields[1])-1)
            elif section == RESIDUECONNECT:
                # Some Amber files have errors in them, incorrectly listing atoms that should not be
                # connected in the first two positions.  We therefore rely on the "connect" section for
                # those, using this block only for other external connections.
                for atom in [int(x)-1 for x in line.split()[2:]]:
                    addExternalBond(residue, atom)

    def process_dat_file(self, inputfile):
        """Read a force field file."""
        block = 0
        continueTorsion = False
        for line in open(inputfile):     
            line = line.strip()
            if block == 0:     # Title
                block += 1
            elif block == 1:   # Mass
                fields = line.split()
                if len(fields) == 0:
                    block += 1
                else:
                    self.masses[fields[0]] = float(fields[1])
            elif block == 2:   # Hydrophilic atoms
                block += 1
            elif block == 3:   # Bonds
                if len(line) == 0:
                    block += 1
                else:
                    fields = line[5:].split()
                    self.bonds.append((line[:2].strip(), line[3:5].strip(), fields[0], fields[1]))
            elif block == 4:   # Angles
                if len(line) == 0:
                    block += 1
                else:
                    fields = line[8:].split()
                    self.angles.append((line[:2].strip(), line[3:5].strip(), line[6:8].strip(), fields[0], fields[1]))
            elif block == 5:   # Torsions
                if len(line) == 0:
                    block += 1
                else:
                    fields = line[11:].split()
                    periodicity = int(float(fields[3]))
                    if continueTorsion:
                        self.torsions[-1] += [float(fields[1])/float(fields[0]), fields[2], abs(periodicity)]
                    else:
                        self.torsions.append([line[:2].strip(), line[3:5].strip(), line[6:8].strip(), line[9:11].strip(), float(fields[1])/float(fields[0]), fields[2], abs(periodicity)])
                    continueTorsion = (periodicity < 0)
            elif block == 6:   # Improper torsions
                if len(line) == 0:
                    block += 1
                else:
                    fields = line[11:].split()
                    self.impropers.append((line[:2].strip(), line[3:5].strip(), line[6:8].strip(), line[9:11].strip(), fields[0], fields[1], fields[2]))
            elif block == 7:   # 10-12 hbond potential
                if len(line) == 0:
                    block += 1
            elif block == 8:   # VDW equivalents
                if len(line) == 0:
                    block += 1
                else:
                    fields = line.split()
                    for atom in fields[1:]:
                        self.vdwEquivalents[atom] = fields[0]
            elif block == 9:   # VDW type
                block += 1
                self.vdwType = line.split()[1]
                if self.vdwType not in ['RE', 'AC']:
                    raise ValueError('Nonbonded type (KINDNB) must be RE or AC') 
            elif block == 10:   # VDW parameters
                if len(line) == 0:
                    block += 1
                else:
                    fields = line.split()
                    self.vdw[fields[0]] = (fields[1], fields[2])

    def process_frc_file(self, inputfile):
        block = ''
        continueTorsion = False
        first = True
        for line in open(inputfile):
            line = line.strip()
            if len(line) == 0 or first:
                block = None
                first = False
            elif block is None:
                block = line
            elif block.startswith('MASS'):
                fields = line.split()
                self.masses[fields[0]] = float(fields[1])
            elif block.startswith('BOND'):
                fields = line[5:].split()
                self.bonds.append((line[:2].strip(), line[3:5].strip(), fields[0], fields[1]))
            elif block.startswith('ANGL'):
                fields = line[8:].split()
                self.angles.append((line[:2].strip(), line[3:5].strip(), line[6:8].strip(), fields[0], fields[1]))
            elif block.startswith('DIHE'):
                fields = line[11:].split()
                periodicity = int(float(fields[3]))
                if continueTorsion:
                    self.torsions[-1] += [float(fields[1])/float(fields[0]), fields[2], abs(periodicity)]
                else:
                    self.torsions.append([line[:2].strip(), line[3:5].strip(), line[6:8].strip(), line[9:11].strip(), float(fields[1])/float(fields[0]), fields[2], abs(periodicity)])
                continueTorsion = (periodicity < 0)
            elif block.startswith('IMPR'):
                fields = line[11:].split()
                self.impropers.append((line[:2].strip(), line[3:5].strip(), line[6:8].strip(), line[9:11].strip(), fields[0], fields[1], fields[2]))
            elif block.startswith('NONB'):
                fields = line.split()
                self.vdw[fields[0]] = (fields[1], fields[2])

    def print_xml(self):
        print self.originance
        print "<ForceField>"
        print " <AtomTypes>"
        for index, type in enumerate(self.types):
            print """  <Type name="%s" class="%s" element="%s" mass="%s"/>""" % (self.type_names[index], type[0], type[1].symbol, type[1].mass.value_in_unit(unit.amu))
        print " </AtomTypes>"
        print " <Residues>"
        for res in sorted(self.residueAtoms):
            print """  <Residue name="%s">""" % res
            for atom in self.residueAtoms[res]:
                atom_name, type_id = tuple(atom)
                atom_type = self.type_names[type_id]
                print "   <Atom name=\"%s\" type=\"%s\"/>" % (atom_name, atom_type)
            if res in self.residueBonds:
                for bond in self.residueBonds[res]:
                    print """   <Bond from="%d" to="%d"/>""" % bond
            if res in self.residueConnections:
                for bond in self.residueConnections[res]:
                    print """   <ExternalBond from="%d"/>""" % bond
            print "  </Residue>"
        print " </Residues>"
        print " <HarmonicBondForce>"
        processed = set()
        for bond in self.bonds:
            signature = (bond[0], bond[1])
            if signature in processed:
                continue
            if any([c in skipClasses for c in signature]):
                continue
            processed.add(signature)
            length = float(bond[3])*0.1
            k = float(bond[2])*2*100*4.184
            print """  <Bond class1="%s" class2="%s" length="%s" k="%s"/>""" % (bond[0], bond[1], str(length), str(k))
        print " </HarmonicBondForce>"
        print " <HarmonicAngleForce>"
        processed = set()
        for angle in self.angles:
            signature = (angle[0], angle[1], angle[2])
            if signature in processed:
                continue
            if any([c in skipClasses for c in signature]):
                continue
            processed.add(signature)
            theta = float(angle[4])*math.pi/180.0
            k = float(angle[3])*2*4.184
            print """  <Angle class1="%s" class2="%s" class3="%s" angle="%s" k="%s"/>""" % (angle[0], angle[1], angle[2], str(theta), str(k))
        print " </HarmonicAngleForce>"
        print " <PeriodicTorsionForce>"
        processed = set()
        for tor in reversed(self.torsions):
            signature = (fix(tor[0]), fix(tor[1]), fix(tor[2]), fix(tor[3]))
            if signature in processed:
                continue
            if any([c in skipClasses for c in signature]):
                continue
            processed.add(signature)
            tag = "  <Proper class1=\"%s\" class2=\"%s\" class3=\"%s\" class4=\"%s\"" % signature
            i = 4
            while i < len(tor):
                index = i/3
                periodicity = int(float(tor[i+2]))
                phase = float(tor[i+1])*math.pi/180.0
                k = tor[i]*4.184
                tag += " periodicity%d=\"%d\" phase%d=\"%s\" k%d=\"%s\"" % (index, periodicity, index, str(phase), index, str(k))
                i += 3
            tag += "/>"
            print tag
        processed = set()
        for tor in reversed(self.impropers):
            signature = (fix(tor[2]), fix(tor[0]), fix(tor[1]), fix(tor[3]))
            if signature in processed:
                continue
            if any([c in skipClasses for c in signature]):
                continue
            processed.add(signature)
            tag = "  <Improper class1=\"%s\" class2=\"%s\" class3=\"%s\" class4=\"%s\"" % signature
            i = 4
            while i < len(tor):
                index = i/3
                periodicity = int(float(tor[i+2]))
                phase = float(tor[i+1])*math.pi/180.0
                k = float(tor[i])*4.184
                tag += " periodicity%d=\"%d\" phase%d=\"%s\" k%d=\"%s\"" % (index, periodicity, index, str(phase), index, str(k))
                i += 3
            tag += "/>"
            print tag
        print " </PeriodicTorsionForce>"
        print """ <NonbondedForce coulomb14scale="%g" lj14scale="%s">""" % (charge14scale, epsilon14scale)
        sigmaScale = 0.1*2.0/(2.0**(1.0/6.0))
        for index, type in enumerate(self.types):
            atomClass = type[0]
            q = type[2]
            if atomClass in self.vdwEquivalents:
                atomClass = self.vdwEquivalents[atomClass]
            if atomClass in self.vdw:
                params = [float(x) for x in self.vdw[atomClass]]
                if self.vdwType == 'RE':
                    sigma = params[0]*sigmaScale
                    epsilon = params[1]*4.184
                else:
                    sigma = (params[0]/params[1])**(1.0/6.0)
                    epsilon = 4.184*params[1]*params[1]/(4*params[0])
            else:
                sigma = 0
                epsilon = 0
            if q != 0 or epsilon != 0:
                print """  <Atom type="%s" charge="%s" sigma="%s" epsilon="%s"/>""" % (self.type_names[index], q, sigma, epsilon)
        print " </NonbondedForce>"
        print "</ForceField>"

    def parse_filenames(self, filenames):
        for inputfile in filenames:
            if inputfile.endswith('.lib') or inputfile.endswith('.off'):
                self.process_library_file(inputfile)
            elif inputfile.endswith('.dat'):
                self.process_dat_file(inputfile)
            elif inputfile.endswith("mol2"):
                self.process_mol2_file(inputfile)
            else:
                self.process_frc_file(inputfile)
        
        self.reduce_atomtypes()

    def reduce_atomtypes(self):
        """Reduce the list of atom self.types.  If multiple hydrogens are bound to the same heavy atom,
        they should all use the same type.
        """

        removeType = [False]*len(self.types)
        for res in self.residueAtoms:
            if res not in self.residueBonds:
                continue
            atomBonds = [[] for atom in self.residueAtoms[res]]
            for bond in self.residueBonds[res]:
                atomBonds[bond[0]].append(bond[1])
                atomBonds[bond[1]].append(bond[0])
            for index, atom in enumerate(self.residueAtoms[res]):
                hydrogens = [x for x in atomBonds[index] if self.types[self.residueAtoms[res][x][1]][1] == element.hydrogen]
                for h in hydrogens[1:]:
                    removeType[self.residueAtoms[res][h][1]] = True
                    self.residueAtoms[res][h][1] = self.residueAtoms[res][hydrogens[0]][1]
        newTypes = []
        replaceWithType = [0]*len(self.types)
        for i in range(len(self.types)):
            if not removeType[i]:
                newTypes.append(self.types[i])
            replaceWithType[i] = len(newTypes)-1
        self.types = newTypes
        for res in self.residueAtoms:
            for atom in self.residueAtoms[res]:
                atom[1] = replaceWithType[atom[1]]

    def set_originance(self):
        self.originance = []
        line = """<!-- %s -->\n""" % "Time and parameters of origin:"
        self.originance.append(line)
        now = datetime.datetime.now()
        line = """<!-- %s -->\n""" % str(now)
        self.originance.append(line)
        line = """<!-- %s -->\n""" % subprocess.list2cmdline(sys.argv[1:])
        self.originance.append(line)        
        self.originance = string.join(self.originance, "")

if __name__ == "__main__":
    amber_parser = AmberParser()
    amber_parser.parse_filenames(sys.argv[1:])
    amber_parser.print_xml()
