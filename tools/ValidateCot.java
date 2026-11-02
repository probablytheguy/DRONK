import java.io.File;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.transform.dom.DOMSource;
import javax.xml.validation.SchemaFactory;

/** Offline validator. Usage: java tools/ValidateCot.java BASE.xsd TRACK.xsd EVENT.cot ...
 * Obtain public upstream schemas separately; no schema/binary redistribution.
 */
class ValidateCot {
    public static void main(String[] args) throws Exception {
        if (args.length < 3) throw new IllegalArgumentException("BASE.xsd TRACK.xsd EVENT.cot ...");
        var factory = SchemaFactory.newInstance(XMLConstants.W3C_XML_SCHEMA_NS_URI);
        factory.setProperty(XMLConstants.ACCESS_EXTERNAL_DTD, "");
        factory.setProperty(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
        var base = factory.newSchema(new File(args[0]));
        var track = factory.newSchema(new File(args[1]));
        var builderFactory = DocumentBuilderFactory.newInstance();
        builderFactory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        builderFactory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
        builderFactory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
        for (int i = 2; i < args.length; i++) {
            var doc = builderFactory.newDocumentBuilder().parse(new File(args[i]));
            base.newValidator().validate(new DOMSource(doc));
            var tracks = doc.getElementsByTagName("track");
            for (int j = 0; j < tracks.getLength(); j++)
                track.newValidator().validate(new DOMSource(tracks.item(j)));
            System.out.println("PASS: base event and available track detail: " + args[i]);
        }
    }
}
